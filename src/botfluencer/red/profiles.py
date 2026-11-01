"""Explicit paused-worker account renames inside the organiser simulation only."""

import asyncio
import hashlib
import re
import unicodedata
from collections import Counter
from typing import Any

import httpx
from sqlalchemy import exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.orm import aliased

from botfluencer.competition.errors import CompetitionHTTPError, RequestDeferred
from botfluencer.competition.pacing import TeamPacer
from botfluencer.competition.standards import SOCIAL_URL, require_open
from botfluencer.competition.transport import CompetitionTransport
from botfluencer.config import Settings
from botfluencer.red.config import RedSettings
from botfluencer.red.executor import ReplyExecutor
from botfluencer.red.store import RedStore
from botfluencer.storage.database import Database
from botfluencer.storage.models import NPCPost, RedCampaign, RedProfileRevision


def fictional_names(
    names: list[str], usernames: set[str], accounts: list[str]
) -> dict[str, tuple[str, str]]:
    """Recombine given/family names; never reuse a stored complete NPC identity."""
    originals = {name.casefold().strip() for name in names}
    parts = []
    for name in sorted(originals):
        words = name.split()
        if len(words) == 2 and all(re.fullmatch(r"[a-z]{2,20}", word) for word in words):
            parts.append((words[0], words[-1]))
    if len(parts) < 10:
        raise ValueError("Insufficient reviewed NPC name patterns")
    used = {name.casefold() for name in usernames}
    result: dict[str, tuple[str, str]] = {}
    firsts = sorted({first for first, _ in parts})
    lasts = sorted({last for _, last in parts})
    for account in sorted(accounts):
        for attempt in range(10000):
            seed = int(hashlib.sha256(f"{account}:{attempt}".encode()).hexdigest(), 16)
            first, last = firsts[seed % len(firsts)], lasts[seed // len(firsts) % len(lasts)]
            display = f"{first.title()} {last.title()}"
            if display.casefold() in originals:
                continue
            # Vary ordinary handle spelling, without a shared generated prefix.
            variants = [first + last, first + "_" + last, first[0] + last, first + last[0]]
            offset = int(hashlib.sha256(account.encode()).hexdigest()[:8], 16) % len(variants)
            username = variants[offset]
            if username.casefold() not in used and len(username) <= 30:
                used.add(username.casefold())
                originals.add(display.casefold())
                result[account] = username, display
                break
        else:
            raise ValueError("Insufficient distinct fictional profile names")
    return result


class ProfileRenamer:
    def __init__(
        self, store: RedStore, executor: ReplyExecutor, connection: AsyncConnection
    ) -> None:
        self.store, self.executor, self.connection = store, executor, connection

    async def _guard(self) -> None:
        self.executor._require_enabled()
        await self.connection.execute(text("SELECT 1"))
        if not await self.store.is_paused():
            raise ValueError("Keep the campaign paused during profile maintenance")
        async with self.store.db.sessions() as session:
            campaign = await session.get(RedCampaign, self.store.scope)
            if campaign is None or campaign.namespace != self.store.namespace:
                raise ValueError("No matching audited campaign")
            if not set(self.executor.sessions) <= set(campaign.accounts):
                raise ValueError("Profile session is outside campaign history")

    async def _request(self, account: str, *, body: dict[str, Any] | None = None) -> Any:
        await self._guard()
        session = self.executor.sessions[account]
        self.executor._require_session_active(session)
        transport = self.executor.transport
        async with asyncio.timeout(transport.settings.operation_timeout_seconds):
            while True:
                try:
                    # The SDK's generic account route uses the shared other-route budget.
                    await transport.pacer.reserve("other_read")
                    break
                except RequestDeferred as error:
                    await asyncio.sleep(min(30, error.seconds))
            await self._guard()
            response = await transport.client.request(
                "GET" if body is None else "PUT",
                SOCIAL_URL + ("/auth/me" if body is None else f"/accounts/{account}"),
                headers={"Authorization": "Bearer " + session.token.get_secret_value()},
                json=body,
            )
            delay = await self.executor._observe_headers(response, "other_read")
            if response.status_code == 429:
                raise RequestDeferred(delay)
            if not 200 <= response.status_code < 300:
                raise CompetitionHTTPError(response.status_code)
            return response.json()

    async def read(self, account: str) -> dict[str, Any]:
        data = await self._request(account)
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("account"), dict)
            or not isinstance(data.get("team"), dict)
            or data["account"].get("account_id") != int(account)
            or data["team"].get("team_id") != self.executor.expected_team_id
        ):
            raise ValueError("Profile session does not match account and team")
        return dict(data["account"])

    async def prepare(self, reason: str) -> list[RedProfileRevision]:
        if not 1 <= len(reason.strip()) <= 500:
            raise ValueError("Provide a bounded operator reason")
        await self._guard()
        async with self.store.db.sessions() as session:
            prior = list(
                await session.scalars(
                    select(RedProfileRevision).where(
                        RedProfileRevision.scope == self.store.scope,
                        RedProfileRevision.account.in_(self.executor.sessions),
                    )
                )
            )
            archive = self.store.config.archive_namespace
            conflict = aliased(NPCPost)
            names = list(
                await session.scalars(
                    select(NPCPost.name)
                    .where(
                        NPCPost.namespace == archive,
                        NPCPost.npc_status == "npc",
                        NPCPost.name.is_not(None),
                        ~exists(
                            select(conflict.post_id).where(
                                conflict.namespace == archive,
                                conflict.author_id == NPCPost.author_id,
                                conflict.npc_status == "player",
                            )
                        ),
                    )
                    .distinct()
                )
            )
            handles = set(
                await session.scalars(
                    select(NPCPost.user_name)
                    .where(
                        NPCPost.namespace == archive,
                        NPCPost.user_name.is_not(None),
                    )
                    .distinct()
                )
            )
        known = {row.account for row in prior}
        profiles = {}
        for account in sorted(self.executor._verified_accounts - known):
            profile = await self.read(account)
            if str(profile.get("username", "")).startswith("ctnsim_"):
                profiles[account] = profile
        # Normalize accents only for constructing a new name; never copy archive text verbatim.
        clean = [
            unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
            for name in names
        ]
        used = {str(h) for h in handles} | {r.username for r in prior}
        generated: dict[str, tuple[str, str]] = {}
        for account in sorted(profiles):
            for _ in range(20):
                candidate = fictional_names(clean, used, [account])[account]
                username, display_name = candidate
                async with asyncio.timeout(
                    self.executor.transport.settings.operation_timeout_seconds
                ):
                    while True:
                        try:
                            data = await self.executor.transport.request(
                                "social",
                                "/accounts",
                                "other_read",
                                params={"q": username, "limit": 1},
                            )
                            break
                        except RequestDeferred as error:
                            await asyncio.sleep(min(30, error.seconds))
                if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                    raise ValueError("Invalid account availability response")
                used.add(username)
                if not data["items"]:
                    generated[account] = candidate
                    clean.append(display_name)
                    break
            else:
                raise ValueError("No available distinct fictional handle")
        async with self.store.db.sessions.begin() as session:
            for account, profile in profiles.items():
                username, display_name = generated[account]
                row = RedProfileRevision(
                    scope=self.store.scope,
                    account=account,
                    previous_username=profile["username"],
                    previous_display_name=profile.get("display_name"),
                    username=username,
                    display_name=display_name,
                    reason=reason.strip(),
                    status="planned",
                )
                session.add(row)
                prior.append(row)
        return prior

    async def status(self, row: RedProfileRevision, value: str) -> None:
        async with self.store.db.sessions.begin() as session:
            saved = await session.get(RedProfileRevision, row.id, with_for_update=True)
            assert saved is not None
            saved.status, saved.updated_at = value, func.now()
        row.status = value

    async def apply(self, row: RedProfileRevision) -> None:
        if row.account not in self.executor._verified_accounts:
            return
        profile = await self.read(row.account)
        if (profile.get("username"), profile.get("display_name")) == (
            row.username,
            row.display_name,
        ):
            await self.status(row, "succeeded")
            return
        if row.status != "planned":
            # Interrupted or ambiguous writes only receive positive read reconciliation.
            if row.status in {"in_flight", "succeeded"}:
                await self.status(row, "unknown")
            return
        if (profile.get("username"), profile.get("display_name")) != (
            row.previous_username,
            row.previous_display_name,
        ):
            await self.status(row, "rejected")
            return
        await self.status(row, "in_flight")
        try:
            await self._request(
                row.account,
                body={
                    "username": row.username,
                    "display_name": row.display_name,
                    "avatar_url": profile.get("avatar_url"),
                },
            )
        except RequestDeferred:
            await self.status(row, "planned")  # Definitive rate rejection or pre-call deferral.
            raise
        except CompetitionHTTPError as error:
            definitive = error.status in {400, 401, 403, 404, 409, 422}
            await self.status(row, "rejected" if definitive else "unknown")
            raise
        except (httpx.HTTPError, TimeoutError, ValueError):
            await self.status(row, "unknown")
            raise
        # Do not trust a write acknowledgement alone: authenticate the identity again.
        profile = await self.read(row.account)
        matches = (profile.get("username"), profile.get("display_name")) == (
            row.username,
            row.display_name,
        )
        await self.status(row, "succeeded" if matches else "unknown")


async def rename_profiles(
    settings: Settings, config: RedSettings, *, reason: str, apply: bool
) -> dict[str, object]:
    if not settings.ctn_network_enabled:
        raise ValueError("Competition access is disabled")
    require_open()
    db = Database(settings)
    transport = CompetitionTransport(settings, TeamPacer(db))
    store = RedStore(db)
    store.config, store.scope = config, transport.pacer.scope
    try:
        async with db.leadership() as connection, asyncio.timeout(1800):
            if not await store.is_paused():
                raise ValueError("Pause and stop the worker before renaming profiles")
            executor = ReplyExecutor(
                transport,
                config.sessions(settings),
                npc_author_ids=frozenset(),
                expected_team_id=config.expected_team_id,
                ends_at=config.ends_at,
                enabled=config.enable_replies,
                disabled_accounts=await store.disabled_accounts(),
            )
            async with asyncio.timeout(config.identity_timeout_seconds):
                await executor.verify_sessions()
            renamer = ProfileRenamer(store, executor, connection)
            rows = await renamer.prepare(reason)
            if apply:
                for row in rows:
                    await renamer.apply(row)
            return {
                "verified_accounts": len(executor._verified_accounts),
                "profiles": dict(Counter(row.status for row in rows)),
                "applied": apply,
            }
    finally:
        await transport.close()
        await db.close()
