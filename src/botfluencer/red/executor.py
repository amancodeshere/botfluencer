"""Narrow SDK 0.5.0 posts and confirmed-NPC replies inside the simulation.

The runtime must persist and claim intent before calling ``execute``. This
adapter makes one publication attempt, with no idempotency or reconciliation
guarantee: an unacknowledged write remains unknown and must never be retried.
"""

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from pydantic import SecretStr

from botfluencer.competition.errors import CompetitionHTTPError, RequestDeferred
from botfluencer.competition.standards import SOCIAL_URL, require_open
from botfluencer.competition.transport import CompetitionTransport
from botfluencer.domain import ActionIntent, ObservationInput, Receipt, Reconciliation
from botfluencer.platform.base import PermanentRejection, SafeRetry
from botfluencer.platform.ctn import CTNPlatform
from botfluencer.red.world import ALLOWED_GIF_URLS, validate_source_url


@dataclass(frozen=True)
class RedSession:
    account_id: int
    token: SecretStr
    expires_at: datetime | None = None


def _positive_id(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("Invalid competition identifier")
    return value


class ReplyExecutor:
    def __init__(
        self,
        transport: CompetitionTransport,
        sessions: Mapping[str, RedSession],
        *,
        npc_author_ids: frozenset[int],
        expected_team_id: int,
        ends_at: datetime,
        enabled: bool = False,
        allow_threads: bool = False,
    ) -> None:
        if ends_at.utcoffset() is None:
            raise ValueError("Reply execution deadline requires an explicit timezone")
        self.transport = transport
        self.sessions = dict(sessions)
        self.enabled = enabled
        self.allow_threads = allow_threads
        self.expected_team_id = _positive_id(expected_team_id)
        self.ends_at = ends_at
        self.npc_author_ids = frozenset(_positive_id(value) for value in npc_author_ids)
        if not self.sessions:
            raise ValueError("Reply execution requires configured account sessions")
        for account, session in self.sessions.items():
            if account != str(_positive_id(session.account_id)):
                raise ValueError("Session account must match its configured numeric identifier")
            if not session.token.get_secret_value().strip():
                raise ValueError("Reply execution requires a nonempty account session token")
            if session.expires_at is not None and session.expires_at.utcoffset() is None:
                raise ValueError("Session expiry requires an explicit timezone")
        if set(self.sessions) != set(self.transport.settings.accounts):
            raise ValueError("Reply sessions must match all configured accounts")
        own_ids = {session.account_id for session in self.sessions.values()}
        self._own_author_ids = frozenset(own_ids)
        if self.npc_author_ids & own_ids:
            raise ValueError("Own competition accounts cannot be NPC targets")
        self._verified_accounts: set[str] = set()
        self._reader = CTNPlatform(transport)

    def _require_enabled(self) -> None:
        if not self.enabled or not self.transport.settings.ctn_network_enabled:
            raise PermanentRejection("Explicit simulation reply and network opt-ins required")
        now = self.transport.clock()
        try:
            require_open(now)
        except RuntimeError:
            raise PermanentRejection("Competition is not open") from None
        if now >= self.ends_at:
            raise PermanentRejection("Simulation reply execution deadline has passed")

    def _require_session_active(self, session: RedSession) -> None:
        if session.expires_at is not None and self.transport.clock() >= session.expires_at:
            raise PermanentRejection("Competition account session has expired")

    async def _observe_headers(self, response: httpx.Response, bucket: str) -> float:
        delay = self.transport.retry_after(response.headers.get("Retry-After"), bucket)
        try:
            limit = int(response.headers["X-RateLimit-Limit"])
            remaining = int(response.headers["X-RateLimit-Remaining"])
            reset = float(response.headers["X-RateLimit-Reset"])
        except (KeyError, ValueError):
            limit, remaining, reset = None, None, 0.0
        await self.transport.pacer.observe(
            bucket,
            limit=limit,
            remaining=remaining,
            reset=reset,
            retry_after=delay if response.status_code == 429 else 0,
        )
        return delay

    async def verify_sessions(self) -> None:
        """Verify private account/team identity before the worker becomes ready."""
        self._require_enabled()
        self._verified_accounts.clear()
        verified: set[str] = set()
        for account, session in self.sessions.items():
            self._require_session_active(session)
            # All of this is before publication. Wait through durable read pacing
            # so several sessions can be verified together without restart livelock.
            async with asyncio.timeout(self.transport.settings.operation_timeout_seconds):
                while True:
                    try:
                        await self.transport.pacer.reserve("other_read")
                    except RequestDeferred as error:
                        await asyncio.sleep(min(30, error.seconds))
                    else:
                        break
            self._require_enabled()
            self._require_session_active(session)
            response = await self.transport.client.get(
                SOCIAL_URL + "/auth/me",
                headers={
                    "Authorization": "Bearer " + session.token.get_secret_value(),
                    "Accept": "application/json",
                },
            )
            delay = await self._observe_headers(response, "other_read")
            if response.status_code == 429:
                raise RequestDeferred(delay)
            if not 200 <= response.status_code < 300:
                raise CompetitionHTTPError(response.status_code)
            data: Any = response.json()
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("account"), dict)
                or not isinstance(data.get("team"), dict)
                or _positive_id(data["account"].get("account_id")) != session.account_id
                or _positive_id(data["team"].get("team_id")) != self.expected_team_id
            ):
                raise ValueError("Account session does not match the configured account and team")
            verified.add(account)
        self._verified_accounts = verified

    async def observe(self) -> list[ObservationInput]:
        return await self._reader.observe()

    async def _read_preflight(self, path: str, *, wait_for_pacing: bool = False) -> Any:
        # This is before the only publication attempt. Repeating an unissued,
        # budget-deferred read is safe and avoids consecutive-read pacing livelock.
        try:
            async with asyncio.timeout(self.transport.settings.operation_timeout_seconds):
                while True:
                    self._require_enabled()
                    try:
                        return await self.transport.request("social", path, "other_read")
                    except RequestDeferred as error:
                        if not wait_for_pacing:
                            raise
                        await asyncio.sleep(min(30, error.seconds))
        except RequestDeferred as error:
            raise SafeRetry(error.seconds) from None
        except CompetitionHTTPError as error:
            if 400 <= error.status < 500 and error.status not in {408, 429}:
                raise PermanentRejection("Competition preflight was rejected") from None
            raise SafeRetry() from None
        except (httpx.TransportError, TimeoutError):
            raise SafeRetry() from None
        except ValueError:
            raise PermanentRejection("Competition preflight returned an invalid document") from None

    async def _reply_coordinates(self, target: Any, target_id: str) -> tuple[int, int]:
        try:
            if not isinstance(target, dict):
                raise ValueError("Unexpected target response")
            post_id = _positive_id(target.get("post_id"))
            author_id = _positive_id(target.get("author_id"))
            root = target.get("root_post_id")
            root_id = _positive_id(post_id if root is None else root)
            if str(post_id) != target_id or author_id not in self.npc_author_ids:
                raise ValueError("Reply target must be a confirmed organiser NPC")
            if target.get("kind") == "post":
                if target.get("reply_to_post_id") is not None or root_id != post_id:
                    raise ValueError("Original target is not a top-level post")
                return post_id, root_id
            if (
                not self.allow_threads
                or target.get("kind") != "reply"
                or root is None
                or root_id == post_id
            ):
                raise ValueError("Target is not an enabled organiser NPC thread reply")
            parent_id = _positive_id(target.get("reply_to_post_id"))
            seen = {post_id}
            allowed_authors = self.npc_author_ids | self._own_author_ids
            # Include the target in the eight-message bound. Every parent is
            # freshly read; archive metadata cannot establish a safe ancestry.
            for _ in range(7):
                if parent_id in seen:
                    raise ValueError("Reply ancestry contains a cycle")
                seen.add(parent_id)
                parent = await self._read_preflight(f"/posts/{parent_id}", wait_for_pacing=True)
                if (
                    not isinstance(parent, dict)
                    or _positive_id(parent.get("post_id")) != parent_id
                    or _positive_id(parent.get("author_id")) not in allowed_authors
                ):
                    raise ValueError("Reply ancestor is not an authorised simulation account")
                parent_root = parent.get("root_post_id")
                if parent_id == root_id:
                    if (
                        parent.get("kind") != "post"
                        or parent.get("reply_to_post_id") is not None
                        or parent.get("repost_of_post_id") is not None
                        or (parent_root is not None and _positive_id(parent_root) != root_id)
                    ):
                        raise ValueError("Reply ancestry does not end at its top-level root")
                    return post_id, root_id
                if parent.get("kind") != "reply" or _positive_id(parent_root) != root_id:
                    raise ValueError("Reply ancestor belongs to a different thread")
                parent_id = _positive_id(parent.get("reply_to_post_id"))
            raise ValueError("Reply ancestry exceeds the eight-message bound")
        except ValueError:
            raise PermanentRejection(
                "Reply target does not have a verified simulation ancestry"
            ) from None

    async def execute(self, intent: ActionIntent) -> Receipt:
        self._require_enabled()
        session = self.sessions.get(intent.account)
        if session is None or intent.account not in self._verified_accounts:
            raise PermanentRejection("Account session has not been verified")
        self._require_session_active(session)
        if intent.draft.kind not in {"reply", "post"} or not intent.draft.text.strip():
            raise PermanentRejection("Only nonempty simulation posts and replies are supported")
        if "@" in intent.draft.text:
            raise PermanentRejection("Simulation publications cannot mention other accounts")
        for match in re.finditer(r"(?:https?://|www\.)[^\s<>]+", intent.draft.text, re.IGNORECASE):
            url = match.group().rstrip(".,;:!?)]}")
            if url in ALLOWED_GIF_URLS:
                continue
            try:
                validate_source_url(url)
            except ValueError:
                raise PermanentRejection(
                    "Publication contains an unreviewed source or media link"
                ) from None
        target_id = intent.draft.target_id
        payload: dict[str, Any] = {"kind": intent.draft.kind, "body": intent.draft.text}
        post_id: int | None = None
        root_id: int | None = None
        if intent.draft.kind == "post":
            if (
                re.fullmatch(r"post:" + re.escape(intent.account) + r":[0-9]{1,20}", target_id)
                is None
            ):
                raise PermanentRejection(
                    "Original post requires its configured account and slot key"
                )
        else:
            if not target_id.isascii() or not target_id.isdecimal() or target_id.startswith("0"):
                raise PermanentRejection(
                    "Reply target must be a positive competition post identifier"
                )
            target = await self._read_preflight(f"/posts/{target_id}")
            post_id, root_id = await self._reply_coordinates(target, target_id)
            payload.update(reply_to_post_id=post_id, root_post_id=root_id)
        lock = await self._read_preflight("/feed/lock", wait_for_pacing=True)
        if not isinstance(lock, dict) or not isinstance(lock.get("locked"), bool):
            raise PermanentRejection("Competition feed lock returned an invalid document")
        if lock["locked"]:
            if lock.get("reason") == "competition_ended":
                raise PermanentRejection("Competition has ended")
            raise SafeRetry(60)
        try:
            await self.transport.pacer.reserve("publish")
        except RequestDeferred as error:
            raise SafeRetry(error.seconds) from None
        # Time may have advanced while waiting for the durable budget or target read.
        self._require_enabled()
        self._require_session_active(session)
        response = await self.transport.client.post(
            SOCIAL_URL + "/posts",
            headers={
                "Authorization": "Bearer " + session.token.get_secret_value(),
                "Accept": "application/json",
            },
            json=payload,
        )
        delay = await self._observe_headers(response, "publish")
        if response.status_code == 429:
            raise SafeRetry(delay)
        if 400 <= response.status_code < 500 and response.status_code != 408:
            raise PermanentRejection("Competition rejected the publication")
        if not 200 <= response.status_code < 300:
            raise CompetitionHTTPError(response.status_code)
        data: Any = response.json()
        if (
            not isinstance(data, dict)
            or _positive_id(data.get("author_id")) != session.account_id
            or data.get("kind") != intent.draft.kind
            or data.get("body") != intent.draft.text
        ):
            raise ValueError("Unexpected competition publication acknowledgement")
        receipt_id = _positive_id(data.get("post_id"))
        if intent.draft.kind == "reply":
            if (
                _positive_id(data.get("reply_to_post_id")) != post_id
                or _positive_id(data.get("root_post_id")) != root_id
            ):
                raise ValueError("Unexpected competition reply acknowledgement")
        else:
            original_root = data.get("root_post_id")
            if (
                data.get("reply_to_post_id") is not None
                or data.get("repost_of_post_id") is not None
                or (original_root is not None and _positive_id(original_root) != receipt_id)
            ):
                raise ValueError("Unexpected competition original-post acknowledgement")
        return Receipt(external_id=str(receipt_id), details={"target_id": target_id})

    async def reconcile(self, intent: ActionIntent) -> Reconciliation:
        # SDK 0.5.0 exposes no idempotency key or authoritative acceptance lookup.
        return Reconciliation(state="unresolved")
