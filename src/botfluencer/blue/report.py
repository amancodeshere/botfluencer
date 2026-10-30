"""Narrow SDK 0.5.0 post-report contract; no publishing/account creation."""

import asyncio
import hashlib
from typing import Any

from sqlalchemy import text

from botfluencer.blue.config import BlueSettings
from botfluencer.blue.store import BlueStore
from botfluencer.competition.errors import CompetitionHTTPError, RequestDeferred
from botfluencer.competition.standards import SOCIAL_URL, require_open
from botfluencer.competition.transport import CompetitionTransport


class Reporter:
    def __init__(
        self, config: BlueSettings, transport: CompetitionTransport, store: BlueStore
    ) -> None:
        self.config, self.transport, self.store = config, transport, store
        self._verified_identity: tuple[int, int, str] | None = None

    async def verify_session(self) -> None:
        if not self.transport.settings.ctn_network_enabled:
            raise ValueError("Explicit competition network opt-in required")
        token = self.config.session_token
        account_id, team_id = self.config.reporter_account_id, self.config.expected_team_id
        if token is None or account_id is None or team_id is None:
            raise ValueError("Reporting requires a configured reporter account and team")
        identity = (
            account_id,
            team_id,
            hashlib.sha256(token.get_secret_value().encode()).hexdigest(),
        )
        if self._verified_identity == identity:
            return
        self._verified_identity = None
        async with asyncio.timeout(self.transport.settings.operation_timeout_seconds):
            while True:
                try:
                    await self.transport.pacer.reserve("other_read")
                except RequestDeferred as error:
                    await asyncio.sleep(min(30, error.seconds))
                else:
                    break
        require_open(self.transport.clock())
        response = await self.transport.client.get(
            SOCIAL_URL + "/auth/me",
            headers={
                "Authorization": "Bearer " + token.get_secret_value(),
                "Accept": "application/json",
            },
        )
        await self.transport.pacer.observe(
            "other_read",
            retry_after=(
                self.transport.retry_after(response.headers.get("Retry-After"), "other_read")
                if response.status_code == 429
                else 0
            ),
        )
        if response.status_code == 429:
            raise RequestDeferred(
                self.transport.retry_after(response.headers.get("Retry-After"), "other_read")
            )
        if not 200 <= response.status_code < 300:
            raise CompetitionHTTPError(response.status_code)
        data: Any = response.json()
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("account"), dict)
            or not isinstance(data.get("team"), dict)
        ):
            raise ValueError("Reporter account session identity is invalid")
        actual_account = data["account"].get("account_id")
        actual_team = data["team"].get("team_id")
        if (
            type(actual_account) is not int
            or type(actual_team) is not int
            or actual_account != account_id
            or actual_team != team_id
            or actual_account not in self.config.own_author_ids
        ):
            raise ValueError("Reporter session does not match the configured account and team")
        self._verified_identity = identity

    @staticmethod
    async def _check_owner(connection: Any, owner_pid: int) -> None:
        if connection.closed or connection.invalidated:
            raise RuntimeError("Report ownership connection was lost")
        if await connection.scalar(text("SELECT pg_backend_pid()")) != owner_pid:
            raise RuntimeError("Report ownership connection changed")
        await connection.commit()

    async def dispatch(self, version: str, *, validated: bool) -> str:
        if not self.config.enable_reports:
            return "shadow"
        if not self.transport.settings.ctn_network_enabled:
            raise ValueError("Explicit competition network opt-in required")
        if not self.config.session_token or not self.config.own_author_ids:
            raise ValueError("Reporting requires an account session token and own author IDs")
        if self.config.auto_report and not validated:
            raise ValueError("Automatic reporting requires a real author-disjoint evaluation")
        require_open()
        await self.verify_session()
        async with self.store.database.leadership("blue-report:" + self.store.scope) as connection:
            owner_pid = await connection.scalar(text("SELECT pg_backend_pid()"))
            await connection.commit()
            # Reserve before persisting execution intent: no request has happened on deferral.
            await self.transport.pacer.reserve("post_report")
            report = await self.store.claim(version, auto=self.config.auto_report)
            if report is None:
                return "idle"
            post_id = int(report["post_id"])
            try:
                await self._check_owner(connection, owner_pid)
                async with self.transport.client.stream(
                    "POST",
                    SOCIAL_URL + f"/posts/{post_id}/report",
                    headers={
                        "Authorization": "Bearer " + self.config.session_token.get_secret_value(),
                        "Accept": "application/json",
                    },
                    json={"reason": "inauthentic", "detail": report["detail"]},
                ) as response:
                    await response.aread()
                    delay = self.transport.retry_after(
                        response.headers.get("Retry-After"), "post_report"
                    )
                    try:
                        limit = int(response.headers["X-RateLimit-Limit"])
                        remaining = int(response.headers["X-RateLimit-Remaining"])
                        reset = float(response.headers["X-RateLimit-Reset"])
                    except (KeyError, ValueError):
                        limit, remaining, reset = None, None, 0.0
                    await self.transport.pacer.observe(
                        "post_report",
                        limit=limit,
                        remaining=remaining,
                        reset=reset,
                        retry_after=delay if response.status_code == 429 else 0,
                    )
                    if response.status_code == 429:
                        await self.store.finish(post_id, "approved")
                        raise RequestDeferred(delay)
                    if not 200 <= response.status_code < 300:
                        status = (
                            "rejected"
                            if 400 <= response.status_code < 500 and response.status_code != 408
                            else "unknown"
                        )
                        if response.status_code in {401, 403}:
                            self._verified_identity = None
                        await self.store.finish(post_id, status, block=response.status_code == 403)
                        raise CompetitionHTTPError(response.status_code)
                    data: Any = response.json()
                    if not isinstance(data, dict):
                        raise ValueError("Unexpected report acknowledgement")
                await self._check_owner(connection, owner_pid)
                await self.store.finish(post_id, "accepted")
                return "accepted"
            except (RequestDeferred, CompetitionHTTPError):
                raise
            except BaseException:
                # A timeout, cancellation, lost leadership, or invalid acknowledgement is ambiguous.
                # If recording fails, startup will conservatively recover in_flight to unknown.
                await self.store.finish(post_id, "unknown")
                raise
