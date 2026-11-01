"""Narrow, durable-authority executor for CTN likes, reposts, and team replies."""

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import httpx

from botfluencer.competition.errors import CompetitionHTTPError, RequestDeferred
from botfluencer.competition.standards import SOCIAL_URL
from botfluencer.domain import ActionIntent, Receipt, Reconciliation
from botfluencer.platform.base import PermanentRejection, SafeRetry
from botfluencer.red.engagement_policy import body_digest, is_reviewed_yes_target
from botfluencer.red.executor import RedSession, ReplyExecutor, _positive_id

EngagementKind = Literal["like", "repost", "team_reply"]
TargetType = Literal["own", "npc"]


@dataclass(frozen=True)
class EngagementAuthority:
    """Durable proof attached to a claimed action by its action ID."""

    action_id: UUID
    account: str
    target_id: str
    kind: EngagementKind
    author_id: int
    target_type: TargetType
    source_action_id: UUID | None = None
    body_hash: str | None = None
    root_id: int | None = None


AuthorityLookup = Callable[[UUID], Awaitable[EngagementAuthority | None]]


class EngagementExecutor(ReplyExecutor):
    """Executes only actions backed by a fresh durable engagement proof."""

    def __init__(
        self,
        *args: Any,
        authority_for_action: AuthorityLookup,
        max_target_age_hours: float = 2,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        if not math.isfinite(max_target_age_hours) or not 0 < max_target_age_hours <= 2:
            raise ValueError("Engagement target age must be positive and at most two hours")
        self.authority_for_action = authority_for_action
        self.max_target_age_hours = max_target_age_hours

    def _validate_authority(
        self, intent: ActionIntent, authority: EngagementAuthority | None
    ) -> tuple[EngagementAuthority, int]:
        if authority is None:
            raise PermanentRejection("Engagement has no durable authority")
        draft = intent.draft
        if (
            authority.action_id != intent.id
            or authority.account != intent.account
            or authority.target_id != draft.target_id
            or authority.kind != draft.kind
            or draft.kind not in {"like", "repost", "team_reply"}
            or authority.target_type not in {"own", "npc"}
        ):
            raise PermanentRejection("Engagement differs from its durable authority")
        if not draft.target_id.isascii() or not draft.target_id.isdecimal():
            raise PermanentRejection("Engagement target must be a positive post identifier")
        try:
            target_id = _positive_id(int(draft.target_id))
            author_id = _positive_id(authority.author_id)
            root_id = None if authority.root_id is None else _positive_id(authority.root_id)
        except ValueError:
            raise PermanentRejection("Engagement authority has invalid coordinates") from None
        if str(target_id) != draft.target_id:
            raise PermanentRejection("Engagement target is not a canonical post identifier")
        if authority.kind == "team_reply":
            if (
                authority.target_type != "own"
                or authority.source_action_id is None
                or authority.body_hash is None
                or root_id not in (None, target_id)
                or not draft.text.strip()
            ):
                raise PermanentRejection("Team reply lacks acknowledged own-post authority")
        elif authority.target_type == "own" and authority.body_hash is None:
            raise PermanentRejection("Own-post engagement lacks a reviewed body digest")
        return authority, author_id

    async def _authority(self, intent: ActionIntent) -> tuple[EngagementAuthority, int]:
        try:
            authority = await self.authority_for_action(intent.id)
        except ValueError:
            raise PermanentRejection("Persisted engagement authority is invalid") from None
        return self._validate_authority(intent, authority)

    def _session_for(self, intent: ActionIntent) -> RedSession:
        self._require_enabled()
        session = self.sessions.get(intent.account)
        if (
            session is None
            or intent.account in self.disabled_accounts
            or intent.account not in self._verified_accounts
        ):
            raise PermanentRejection("Account session has not been verified")
        self._require_session_active(session)
        return session

    async def _account_read(
        self, account: str, session: RedSession, path: str, *, wait_for_pacing: bool = False
    ) -> Any:
        try:
            while True:
                try:
                    await self.transport.pacer.reserve("other_read")
                except RequestDeferred as error:
                    if not wait_for_pacing:
                        raise SafeRetry(error.seconds) from None
                    await asyncio.sleep(min(30, error.seconds))
                else:
                    break
            response = await self.transport.client.get(
                SOCIAL_URL + path,
                headers={
                    "Authorization": "Bearer " + session.token.get_secret_value(),
                    "Accept": "application/json",
                },
            )
            delay = await self._observe_headers(response, "other_read")
        except SafeRetry:
            raise
        except (httpx.TransportError, TimeoutError):
            raise SafeRetry() from None
        if response.status_code == 429:
            raise SafeRetry(delay)
        if response.status_code in (401, 403):
            await self._disable(account, "authentication_rejected")
            raise PermanentRejection("Competition account authentication was rejected")
        if 400 <= response.status_code < 500:
            raise PermanentRejection("Competition engagement read was rejected")
        if not 200 <= response.status_code < 300:
            raise SafeRetry()
        try:
            return response.json()
        except ValueError:
            raise PermanentRejection("Competition engagement read was malformed") from None

    async def _read_target(
        self, target_id: int, actor_id: int, authority: EngagementAuthority, author_id: int
    ) -> dict[str, Any]:
        target = await self._read_preflight(f"/posts/{target_id}")
        try:
            valid_id = isinstance(target, dict) and _positive_id(target.get("post_id")) == target_id
        except ValueError:
            valid_id = False
        if not valid_id or not isinstance(target, dict):
            raise PermanentRejection("Engagement target returned an invalid post")
        try:
            actual_author = _positive_id(target.get("author_id"))
        except ValueError:
            raise PermanentRejection("Engagement target returned an invalid author") from None
        if actual_author != author_id:
            raise PermanentRejection("Engagement target author differs from durable authority")
        if actual_author == actor_id:
            raise PermanentRejection("An account cannot engage with its own post")
        root = target.get("root_post_id")
        try:
            root_matches = root is None or _positive_id(root) == target_id
        except ValueError:
            raise PermanentRejection(
                "Engagement target has invalid top-level coordinates"
            ) from None
        is_top_level = (
            target.get("kind") == "post"
            and target.get("reply_to_post_id") is None
            and target.get("repost_of_post_id") is None
            and root_matches
        )
        if not is_top_level:
            raise PermanentRejection("Engagement target must be a top-level post")
        body = target.get("body")
        if not isinstance(body, str) or not body.strip():
            raise PermanentRejection("Engagement target has no reviewable body")
        if authority.target_type == "own":
            if actual_author not in self._own_author_ids:
                raise PermanentRejection("Own-post authority does not identify a team account")
            if body_digest(body) != authority.body_hash:
                raise PermanentRejection("Own post changed after its engagement was reviewed")
        else:
            if actual_author not in self.npc_author_ids or actual_author in self._own_author_ids:
                raise PermanentRejection("NPC engagement target is not a reviewed NPC")
            if not is_reviewed_yes_target(body):
                raise PermanentRejection("NPC post does not match the reviewed Vote Yes policy")
            if authority.body_hash is None or body_digest(body) != authority.body_hash:
                raise PermanentRejection("NPC post changed after its engagement was reviewed")
            created_at = target.get("created_at")
            try:
                if not isinstance(created_at, str):
                    raise ValueError("Missing target timestamp")
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                now = self.transport.clock()
                if created.utcoffset() is None or now.utcoffset() is None:
                    raise ValueError("Target timestamp must be timezone-aware")
                age = now.astimezone(UTC) - created.astimezone(UTC)
                if age < timedelta(0) or age > timedelta(hours=self.max_target_age_hours):
                    raise ValueError("Target timestamp is outside the freshness window")
            except (OverflowError, TypeError, ValueError):
                raise PermanentRejection(
                    "NPC engagement target is stale or has an invalid timestamp"
                ) from None
        return target

    async def _mine_state(
        self,
        account: str,
        session: RedSession,
        target_id: int,
        kind: Literal["like", "repost"],
    ) -> tuple[bool, str | None]:
        path = (
            f"/likes/mine?post_ids={target_id}"
            if kind == "like"
            else f"/reposts/mine?post_ids={target_id}"
        )
        data = await self._account_read(account, session, path)
        if kind == "like":
            ids = data.get("post_ids") if isinstance(data, dict) else None
            if not isinstance(ids, list) or any(
                not isinstance(value, int) or isinstance(value, bool) for value in ids
            ):
                raise PermanentRejection("Competition like-state response was malformed")
            return target_id in ids, str(target_id) if target_id in ids else None
        reposts = data.get("reposts") if isinstance(data, dict) else None
        if not isinstance(reposts, list):
            raise PermanentRejection("Competition repost-state response was malformed")
        for item in reposts:
            if not isinstance(item, dict):
                raise PermanentRejection("Competition repost-state response was malformed")
            try:
                post_id = _positive_id(item.get("post_id"))
            except ValueError:
                raise PermanentRejection(
                    "Competition repost-state response was malformed"
                ) from None
            if post_id == target_id:
                try:
                    repost_id = _positive_id(item.get("repost_post_id"))
                except ValueError:
                    raise PermanentRejection(
                        "Competition repost-state response was malformed"
                    ) from None
                return True, str(repost_id)
        return False, None

    async def _check_feed_lock(self) -> None:
        lock = await self._read_preflight("/feed/lock", wait_for_pacing=True)
        if not isinstance(lock, dict) or not isinstance(lock.get("locked"), bool):
            raise PermanentRejection("Competition feed lock returned an invalid document")
        if lock["locked"]:
            if lock.get("reason") == "competition_ended":
                raise PermanentRejection("Competition has ended")
            raise SafeRetry(60)

    async def execute(self, intent: ActionIntent) -> Receipt:
        if intent.draft.kind in {"post", "reply"}:
            return await super().execute(intent)
        session = self._session_for(intent)
        authority, author_id = await self._authority(intent)
        target_id = _positive_id(int(intent.draft.target_id))
        if session.account_id == author_id:
            raise PermanentRejection("An account cannot engage with its own post")
        target = await self._read_target(target_id, session.account_id, authority, author_id)
        kind = authority.kind
        if kind == "like" or kind == "repost":
            already_present, receipt_id = await self._mine_state(
                intent.account, session, target_id, kind
            )
            if already_present:
                return Receipt(
                    external_id=receipt_id or str(target_id),
                    details={"kind": kind, "target_id": str(target_id), "preexisting": True},
                )
        await self._check_feed_lock()
        bucket = "like" if kind == "like" else "publish"
        try:
            await self.transport.pacer.reserve(bucket)
        except RequestDeferred as error:
            raise SafeRetry(error.seconds) from None
        target = await self._read_target(target_id, session.account_id, authority, author_id)
        current_authority, current_author = await self._authority(intent)
        if current_authority != authority or current_author != author_id:
            raise PermanentRejection("Engagement authority changed before execution")
        self._require_enabled()
        self._require_session_active(session)
        if session.account_id == author_id:
            raise PermanentRejection("An account cannot engage with its own post")
        headers = {
            "Authorization": "Bearer " + session.token.get_secret_value(),
            "Accept": "application/json",
        }
        if kind == "like":
            response = await self.transport.client.post(
                SOCIAL_URL + f"/posts/{target_id}/like", headers=headers
            )
        else:
            payload: dict[str, Any]
            if kind == "repost":
                payload = {"kind": "repost", "repost_of_post_id": target_id}
            else:
                if target.get("kind") != "post" or authority.target_type != "own":
                    raise PermanentRejection(
                        "Team replies require an acknowledged own top-level post"
                    )
                payload = {
                    "kind": "reply",
                    "body": intent.draft.text,
                    "reply_to_post_id": target_id,
                    "root_post_id": target_id,
                }
            response = await self.transport.client.post(
                SOCIAL_URL + "/posts", headers=headers, json=payload
            )
        delay = await self._observe_headers(response, bucket)
        if response.status_code == 429:
            raise SafeRetry(delay)
        if response.status_code in (401, 403):
            await self._disable(intent.account, "authentication_rejected")
            raise PermanentRejection("Competition account authentication was rejected")
        if 400 <= response.status_code < 500 and response.status_code != 408:
            raise PermanentRejection("Competition rejected the engagement")
        if not 200 <= response.status_code < 300:
            raise CompetitionHTTPError(response.status_code)
        if kind == "like":
            if response.content:
                try:
                    data = response.json()
                except ValueError:
                    raise ValueError("Unexpected competition like acknowledgement") from None
                if not isinstance(data, dict):
                    raise ValueError("Unexpected competition like acknowledgement")
            return Receipt(
                external_id=str(target_id), details={"kind": kind, "target_id": str(target_id)}
            )
        data = response.json()
        post_id = _positive_id(data.get("post_id")) if isinstance(data, dict) else 0
        if not isinstance(data, dict) or _positive_id(data.get("author_id")) != session.account_id:
            raise ValueError("Unexpected competition engagement acknowledgement")
        if kind == "repost":
            if (
                data.get("kind") != "repost"
                or _positive_id(data.get("repost_of_post_id")) != target_id
            ):
                raise ValueError("Unexpected competition repost acknowledgement")
        elif (
            data.get("kind") != "reply"
            or data.get("body") != intent.draft.text
            or _positive_id(data.get("reply_to_post_id")) != target_id
            or _positive_id(data.get("root_post_id")) != target_id
        ):
            raise ValueError("Unexpected competition team-reply acknowledgement")
        return Receipt(
            external_id=str(post_id), details={"kind": kind, "target_id": str(target_id)}
        )

    async def reconcile(self, intent: ActionIntent) -> Reconciliation:
        if intent.draft.kind not in {"like", "repost"}:
            return Reconciliation(state="unresolved")
        session = self.sessions.get(intent.account)
        if (
            session is None
            or intent.account in self.disabled_accounts
            or intent.account not in self._verified_accounts
        ):
            return Reconciliation(state="unresolved")
        try:
            authority, author_id = await self._authority(intent)
            target_id = _positive_id(int(intent.draft.target_id))
            if session.account_id == author_id:
                return Reconciliation(state="unresolved")
            kind = authority.kind
            if kind != "like" and kind != "repost":
                return Reconciliation(state="unresolved")
            present, receipt_id = await self._mine_state(intent.account, session, target_id, kind)
            if not present:
                return Reconciliation(state="unresolved")
            return Reconciliation(
                state="applied",
                receipt=Receipt(
                    external_id=receipt_id or str(target_id),
                    details={"kind": kind, "target_id": str(target_id), "reconciled": True},
                ),
            )
        except (
            PermanentRejection,
            SafeRetry,
            CompetitionHTTPError,
            httpx.TransportError,
            TimeoutError,
            ValueError,
        ):
            return Reconciliation(state="unresolved")
