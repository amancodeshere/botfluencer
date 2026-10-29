"""One-shot organiser-simulation registration with private recovery records.

An existing recovery file is never reused or overwritten. Each generated
username is submitted at most once; operators must inspect an interrupted or
ambiguous registration privately rather than run registration again.
"""

import asyncio
import json
import os
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from botfluencer.competition.errors import RequestDeferred
from botfluencer.competition.pacing import TeamPacer
from botfluencer.competition.standards import SOCIAL_URL, require_open
from botfluencer.competition.transport import CompetitionTransport
from botfluencer.config import Settings
from botfluencer.storage.database import Database


def _positive_id(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("Invalid competition identifier")
    return value


def _save(path: Path, state: dict[str, Any], *, initial: bool = False) -> None:
    payload = json.dumps(state, indent=2) + "\n"
    if initial:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _sync_directory(path.parent)
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".red-accounts-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


async def _reserve(transport: CompetitionTransport, bucket: str) -> None:
    # Only budget reservations repeat. No HTTP request is retried.
    async with asyncio.timeout(max(60, transport.settings.operation_timeout_seconds)):
        while True:
            try:
                await transport.pacer.reserve(bucket)
            except RequestDeferred as error:
                await asyncio.sleep(min(30, error.seconds))
            else:
                return


async def _headers(transport: CompetitionTransport, response: httpx.Response, bucket: str) -> None:
    try:
        limit = int(response.headers["X-RateLimit-Limit"])
        remaining = int(response.headers["X-RateLimit-Remaining"])
        reset = float(response.headers["X-RateLimit-Reset"])
    except (KeyError, ValueError):
        limit, remaining, reset = None, None, 0.0
    await transport.pacer.observe(
        bucket,
        limit=limit,
        remaining=remaining,
        reset=reset,
        retry_after=(
            transport.retry_after(response.headers.get("Retry-After"), bucket)
            if response.status_code == 429
            else 0
        ),
    )


def _prepare(output: Path, count: int, expected_team_id: int) -> tuple[Path, dict[str, Any]]:
    project = Path(__file__).resolve().parents[3]
    path = output.expanduser().absolute()
    if path.is_symlink() or path.exists():
        raise ValueError("Recovery file already exists; inspect it privately before proceeding")
    path = path.resolve()
    if path.is_relative_to(project):
        raise ValueError("Account recovery credentials must be stored outside the checkout")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    entries: list[dict[str, Any]] = [
        {
            "username": "ctnsim_" + secrets.token_hex(8),
            "password": secrets.token_urlsafe(32),
            "display_name": f"CTN simulation {index + 1}",
            "status": "planned",
        }
        for index in range(count)
    ]
    state: dict[str, Any] = {
        "version": 1,
        "expected_team_id": expected_team_id,
        "created_at": datetime.now(UTC).isoformat(),
        "accounts": entries,
    }
    _save(path, state, initial=True)
    return path, state


async def provision(
    settings: Settings,
    output: Path,
    count: int = 4,
    *,
    expected_team_id: int,
) -> dict[str, Any]:
    """Create a bounded set of neutral simulation accounts; return IDs/counts only."""
    if not settings.ctn_network_enabled or settings.ctn_team_code is None:
        raise ValueError("Registration requires explicit network opt-in and a team code")
    require_open()
    _positive_id(expected_team_id)
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count < 1
        or settings.ctn_max_total_accounts is None
        or settings.ctn_max_active_accounts is None
        or count > min(settings.ctn_max_total_accounts, settings.ctn_max_active_accounts)
    ):
        raise ValueError("Registration count must fit the verified account allowances")
    path, state = _prepare(output, count, expected_team_id)
    entries: list[dict[str, Any]] = state["accounts"]
    database: Database | None = None
    transport: CompetitionTransport | None = None
    current: dict[str, Any] | None = None
    try:
        database = Database(settings)
        transport = CompetitionTransport(settings, TeamPacer(database))
        for entry in entries:
            current = entry
            await _reserve(transport, "auth")
            require_open(transport.clock())
            entry["status"] = "registering"
            _save(path, state)
            response = await transport.client.post(
                SOCIAL_URL + "/auth/register",
                json={
                    "username": entry["username"],
                    "password": entry["password"],
                    "team_key": settings.ctn_team_code.get_secret_value(),
                    "display_name": entry["display_name"],
                },
                headers={"Accept": "application/json"},
            )
            if not 200 <= response.status_code < 300:
                entry["status"] = (
                    "rejected"
                    if 400 <= response.status_code < 500 and response.status_code != 408
                    else "unknown"
                )
                entry["http_status"] = response.status_code
                _save(path, state)
                await _headers(transport, response, "auth")
                raise RuntimeError("Registration was not accepted")
            data: Any = response.json()
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("account"), dict)
                or not isinstance(data.get("token"), str)
                or not data["token"].strip()
            ):
                raise ValueError("Invalid registration acknowledgement")
            account_id = _positive_id(data["account"].get("account_id"))
            entry.update(status="registered", account_id=account_id, token=data["token"])
            # Preserve issued credentials even if another acknowledgement field is invalid.
            _save(path, state)
            expiry = data.get("expires_at")
            if expiry is not None:
                if not isinstance(expiry, str):
                    raise ValueError("Invalid session expiry")
                parsed = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                if parsed.utcoffset() is None or parsed <= transport.clock():
                    raise ValueError("Registration session already expired")
            entry["expires_at"] = expiry
            # Persist acknowledged credentials before pacing/database work or another request.
            _save(path, state)
            await _headers(transport, response, "auth")
            # Retain the conservative 13-second spacing even if headers advertise 12.
            await transport.pacer.observe("auth", retry_after=13)
            await _reserve(transport, "other_read")
            identity = await transport.client.get(
                SOCIAL_URL + "/auth/me",
                headers={"Authorization": "Bearer " + entry["token"], "Accept": "application/json"},
            )
            await _headers(transport, identity, "other_read")
            if not 200 <= identity.status_code < 300:
                raise RuntimeError("Account identity could not be verified")
            me: Any = identity.json()
            if (
                not isinstance(me, dict)
                or not isinstance(me.get("account"), dict)
                or not isinstance(me.get("team"), dict)
                or _positive_id(me["account"].get("account_id")) != account_id
                or _positive_id(me["team"].get("team_id")) != expected_team_id
            ):
                raise ValueError("Registered account identity does not match the expected team")
            entry["status"] = "verified"
            _save(path, state)
    except BaseException as error:
        if current is not None and current["status"] == "registering":
            current["status"] = "unknown"
            _save(path, state)
        if isinstance(error, asyncio.CancelledError):
            raise
        raise RuntimeError(
            "Account provisioning stopped; inspect the private recovery file"
        ) from None
    finally:
        if transport is not None:
            await transport.close()
        if database is not None:
            await database.close()
    return {
        "requested": count,
        "registered": sum("account_id" in entry for entry in entries),
        "verified": sum(entry["status"] == "verified" for entry in entries),
        "account_ids": [entry["account_id"] for entry in entries if "account_id" in entry],
    }
