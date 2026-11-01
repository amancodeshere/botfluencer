"""Bounded visibility experiments using acknowledged own and reviewed NPC posts."""

import asyncio
import hashlib
import time
from typing import Any, Literal, cast

from botfluencer.competition.errors import RequestDeferred
from botfluencer.competition.transport import CompetitionTransport
from botfluencer.red.config import RedSettings
from botfluencer.red.engagement_policy import TEAM_REPLY_TEXTS
from botfluencer.red.engagement_store import EngagementStore
from botfluencer.runtime.events import event


class EngagementPlanner:
    def __init__(
        self, store: EngagementStore, transport: CompetitionTransport, config: RedSettings
    ) -> None:
        self.store, self.transport, self.config = store, transport, config
        self.npc_ids: frozenset[int] = frozenset()
        self.next_refresh = 0.0
        self.ranked_until = 0.0
        self.ranked: frozenset[int] = frozenset()
        self.refresh_lock = asyncio.Lock()

    async def refresh_trending(self) -> None:
        now = time.monotonic()
        if now < self.next_refresh:
            return
        try:
            data = await self.transport.request(
                "social", "/posts", "feed", params={"sort": "trending", "limit": 20}
            )
        except RequestDeferred as error:
            self.next_refresh = now + max(1, min(error.seconds, 300))
            return
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ValueError("Trending feed returned an invalid page")
        ids: list[int] = []
        for item in items:
            post_id = item.get("post_id") if isinstance(item, dict) else None
            if not isinstance(post_id, int) or isinstance(post_id, bool) or post_id < 1:
                raise ValueError("Trending feed returned an invalid post identifier")
            if post_id not in ids:
                ids.append(post_id)
        await self.store.record_trending_snapshot(ids, self.transport.clock())
        self.ranked = frozenset(ids)
        self.ranked_until = time.monotonic() + self.config.trending_refresh_seconds
        self.next_refresh = self.ranked_until
        event("red_trending_observed", posts=len(ids))

    async def prepare(self, *, own_only: bool, kinds: tuple[str, ...] | None = None) -> bool:
        """Reserve at most one action; only the worker dispatcher performs writes."""
        if (
            not self.config.engagement_enabled
            or not self.config.starts_at <= self.transport.clock() < self.config.ends_at
            or await self.store.is_paused()
        ):
            return False
        async with self.refresh_lock:
            await self.refresh_trending()
        if own_only:
            candidates = await self.store.own_candidates()
            candidates.sort(
                key=lambda candidate: candidate["created_at"],
                reverse=getattr(self.config, "parallel_lanes", False),
            )
        else:
            candidates = await self.store.npc_candidates(
                self.npc_ids, limit=min(self.config.engagement_scan_limit, 8)
            )
        ranked = self.ranked if time.monotonic() < self.ranked_until else frozenset()
        for candidate in candidates[: self.config.engagement_scan_limit]:
            if candidate["target_id"] in ranked and not (
                own_only and getattr(self.config, "parallel_lanes", False)
            ):
                continue
            if await self._reserve_candidate(candidate, kinds=kinds):
                return True
        return False

    async def _reserve_candidate(
        self, candidate: dict[str, Any], *, kinds: tuple[str, ...] | None = None
    ) -> bool:
        limits = {
            "like": self.config.engagement_likes_per_target,
            "repost": self.config.engagement_reposts_per_target,
            "team_reply": self.config.engagement_comments_per_target,
        }
        # A first like, a repost and a substantive reply precede further likes.
        stages = [("like", 1), ("repost", 1), ("team_reply", 1), ("like", limits["like"])]
        if getattr(self.config, "parallel_lanes", False):
            stages.append(("repost", limits["repost"]))
        reserved = candidate.get("reserved", {})
        for name, stage_limit in stages:
            if kinds is not None and name not in kinds:
                continue
            if name == "team_reply" and candidate["target_type"] != "own":
                continue
            kind = cast(Literal["like", "repost", "team_reply"], name)
            state = reserved.get(kind, {})
            actors = set(state.get("actors", []))
            if state.get("count", 0) >= min(stage_limit, limits[kind]):
                continue
            choices = [
                actor
                for actor in candidate["healthy_accounts"]
                if actor not in actors and actor != str(candidate["author_id"])
            ]
            if not choices:
                continue
            target_id = candidate["target_id"]
            account = min(
                choices,
                key=lambda actor: hashlib.sha256(f"{target_id}:{kind}:{actor}".encode()).digest(),
            )
            reply_index = target_id % len(TEAM_REPLY_TEXTS)
            planned = await self.store.reserve_engagement(
                account,
                target_id,
                kind,
                target_type=candidate["target_type"],
                author_id=candidate["author_id"],
                source_action_id=candidate.get("source_action_id"),
                body_hash=candidate["body_hash"],
                text=TEAM_REPLY_TEXTS[reply_index] if kind == "team_reply" else "",
            )
            if planned is not None:
                event("red_engagement_reserved", kind=kind, target_type=candidate["target_type"])
                return True
        return False
