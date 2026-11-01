"""Durable authorisation and reservation for bounded CTN engagement actions."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import exists, func, select

from botfluencer.domain import ActionIntent, Draft
from botfluencer.red.engagement_policy import (
    body_digest,
    is_reviewed_team_reply,
    is_reviewed_yes_target,
)
from botfluencer.red.store import RedStore
from botfluencer.storage.models import (
    Action,
    Control,
    Decision,
    EngagementReservation,
    NPCPost,
    Observation,
    Outcome,
    RedAccountControl,
    RedCampaign,
    TrendingSnapshot,
    UsageEvent,
)

EngagementKind = Literal["like", "repost", "team_reply"]
TargetType = Literal["own", "npc"]
ENGAGEMENT_STRATEGY_VERSION = "red-engagement-v1"


class EngagementStore(RedStore):
    """Composition layer over RedStore's existing action, budget and control ledgers."""

    async def unknown_engagements(self) -> list[ActionIntent]:
        """Bounded positive-state reconciliation; old unknowns are never retried."""
        async with self.db.sessions.begin() as session:
            actions = await session.scalars(
                select(Action)
                .where(
                    Action.namespace == self.namespace,
                    Action.status == "unknown",
                    Action.payload["kind"].as_string().in_(("like", "repost")),
                    Action.created_at >= func.now() - timedelta(hours=2),
                    Action.next_attempt_at <= func.now(),
                )
                .order_by(Action.next_attempt_at, Action.created_at, Action.id)
                .limit(4)
                .with_for_update(skip_locked=True)
            )
            selected = list(actions)
            now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            for action in selected:
                # Unknown actions cannot be claimed. This timestamp schedules
                # another read, never a retry of the original external write.
                action.next_attempt_at = now + timedelta(seconds=120)
            return [self._intent(action) for action in selected]

    async def record_trending_snapshot(
        self, ranked_ids: Sequence[int], observed_at: datetime | None = None
    ) -> UUID:
        """Persist identifiers and ranks only; candidate text remains elsewhere."""
        ids = list(ranked_ids)
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in ids):
            raise ValueError("Trending snapshot identifiers must be positive integers")
        if len(ids) != len(set(ids)):
            raise ValueError("Trending snapshot identifiers must be unique")
        if observed_at is not None and observed_at.utcoffset() is None:
            raise ValueError("Trending snapshot time must include a timezone")
        snapshot_id = uuid4()
        values: dict[str, Any] = {
            "id": snapshot_id,
            "namespace": self.namespace,
            "ranked_ids": ids,
            "ranks": {str(post_id): rank for rank, post_id in enumerate(ids, start=1)},
        }
        if observed_at is not None:
            values["observed_at"] = observed_at
        async with self.db.sessions.begin() as session:
            session.add(TrendingSnapshot(**values))
        return snapshot_id

    async def _fresh_trending(
        self, session: Any, now: datetime, max_age: timedelta
    ) -> TrendingSnapshot | None:
        snapshot = await session.scalar(
            select(TrendingSnapshot)
            .where(
                TrendingSnapshot.namespace == self.namespace,
                TrendingSnapshot.observed_at >= now - max_age,
                TrendingSnapshot.observed_at <= now,
            )
            .order_by(TrendingSnapshot.observed_at.desc(), TrendingSnapshot.id.desc())
            .limit(1)
        )
        return cast(TrendingSnapshot | None, snapshot)

    async def own_candidates(self) -> list[dict[str, Any]]:
        """Return recent accepted own originals; ranking metadata is audit-only."""
        max_age = timedelta(hours=getattr(self.config, "engagement_max_age_hours", 2))
        async with self.db.sessions() as session:
            now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            snapshot = await self._fresh_trending(session, now, max_age)
            ranks = snapshot.ranks if snapshot is not None else {}
            campaign = await session.get(RedCampaign, self.scope)
            if campaign is None:
                return []
            disabled = set(
                await session.scalars(
                    select(RedAccountControl.account).where(
                        RedAccountControl.scope == self.scope,
                        RedAccountControl.disabled.is_(True),
                    )
                )
            )
            allowed = set(campaign.accounts) & set(self.settings.accounts) - disabled
            if not allowed:
                return []
            receipts = await session.execute(
                select(Action, Outcome)
                .join(Outcome, Outcome.action_id == Action.id)
                .where(
                    Action.namespace == self.namespace,
                    Action.account.in_(allowed),
                    Action.status == "succeeded",
                    Action.payload["kind"].as_string() == "post",
                    Outcome.kind == "accepted",
                    Outcome.created_at >= now - max_age,
                    Outcome.created_at <= now,
                )
                .order_by(Outcome.created_at.desc(), Action.id)
            )
            by_target: dict[int, tuple[Action, Outcome]] = {}
            for action, outcome in receipts.all():
                external_id = outcome.payload.get("external_id")
                body = action.payload.get("text")
                if (
                    not isinstance(external_id, str)
                    or not external_id.isascii()
                    or not external_id.isdecimal()
                    or external_id.startswith("0")
                    or not isinstance(body, str)
                    or not body.strip()
                    or action.payload.get("reply_to_post_id") is not None
                    or action.payload.get("repost_of_post_id") is not None
                ):
                    continue
                target_id = int(external_id)
                if target_id not in by_target:
                    by_target[target_id] = (action, outcome)
            target_ids = list(by_target)
            reservation_state = await self._reservation_state(session, target_ids)
            healthy_accounts = sorted(allowed)
            candidates = []
            for target_id, (action, outcome) in by_target.items():
                body = action.payload["text"]
                candidates.append(
                    {
                        "target_id": target_id,
                        "author_id": int(action.account),
                        "source_action_id": action.id,
                        "body_hash": body_digest(body),
                        "body": body,
                        "text": body,
                        "created_at": outcome.created_at,
                        "target_type": "own",
                        "trend_rank": ranks.get(str(target_id)),
                        "trending_observed_at": snapshot.observed_at
                        if snapshot is not None
                        else None,
                        "healthy_accounts": [
                            candidate_account
                            for candidate_account in healthy_accounts
                            if candidate_account != action.account
                        ],
                        "reserved": reservation_state.get(target_id, {}),
                        **reservation_state.get(target_id, {}),
                    }
                )
            return candidates

    async def npc_candidates(self, npc_ids: frozenset[int], limit: int = 8) -> list[dict[str, Any]]:
        """Return only recent, top-level reviewed NPC posts passing finite policy."""
        if isinstance(limit, bool) or not 1 <= limit <= 8:
            raise ValueError("NPC engagement candidate limit must be from one to eight")
        max_age = timedelta(hours=getattr(self.config, "engagement_max_age_hours", 2))
        async with self.db.sessions() as session:
            now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            campaign = await session.get(RedCampaign, self.scope)
            if campaign is None:
                return []
            disabled = set(
                await session.scalars(
                    select(RedAccountControl.account).where(
                        RedAccountControl.scope == self.scope,
                        RedAccountControl.disabled.is_(True),
                    )
                )
            )
            healthy = sorted(set(campaign.accounts) & set(self.settings.accounts) - disabled)
            if not healthy:
                return []
            reviewed_authors = set(
                await session.scalars(
                    select(NPCPost.author_id)
                    .where(
                        NPCPost.namespace == self.config.archive_namespace,
                        NPCPost.npc_status == "npc",
                    )
                    .distinct()
                )
            )
            conflicted = set(
                await session.scalars(
                    select(NPCPost.author_id)
                    .where(
                        NPCPost.namespace == self.config.archive_namespace,
                        NPCPost.npc_status == "player",
                    )
                    .distinct()
                )
            )
            rows = await session.scalars(
                select(NPCPost)
                .where(
                    NPCPost.namespace == self.config.archive_namespace,
                    NPCPost.kind == "post",
                    NPCPost.author_id.in_((set(npc_ids) & reviewed_authors) - conflicted),
                    NPCPost.post.is_not(None),
                    NPCPost.time_stamp >= now - max_age,
                    NPCPost.time_stamp <= now,
                )
                .order_by(NPCPost.time_stamp.desc(), NPCPost.post_id.desc())
                .limit(32)
            )
            candidates = [
                row
                for row in rows
                if row.post
                and row.post.strip()
                and is_reviewed_yes_target(row.post)
                and str(row.author_id) not in campaign.accounts
            ][:limit]
            state = await self._reservation_state(session, [row.post_id for row in candidates])
            return [
                {
                    "target_id": row.post_id,
                    "author_id": row.author_id,
                    "source_action_id": None,
                    "body_hash": body_digest(row.post or ""),
                    "body": row.post,
                    "text": row.post,
                    "created_at": row.time_stamp,
                    "target_type": "npc",
                    "healthy_accounts": healthy,
                    "reserved": state.get(row.post_id, {}),
                    **state.get(row.post_id, {}),
                }
                for row in candidates
            ]

    async def _reservation_state(
        self, session: Any, target_ids: list[int]
    ) -> dict[int, dict[str, Any]]:
        if not target_ids:
            return {}
        rows = await session.scalars(
            select(EngagementReservation).where(
                EngagementReservation.scope == self.scope,
                EngagementReservation.target_id.in_(target_ids),
            )
        )
        state: dict[int, dict[str, Any]] = {}
        for reservation in rows:
            entry = state.setdefault(reservation.target_id, {})
            reserved = entry.setdefault("reserved", {})
            kind_state = reserved.setdefault(reservation.kind, {"actors": [], "count": 0})
            kind_state["actors"].append(reservation.account)
            kind_state["count"] += 1
        return state

    async def _accepted_original(
        self,
        session: Any,
        source_action_id: UUID,
        target_id: int,
        author_id: int,
        now: datetime,
        max_age: timedelta,
    ) -> tuple[Action, str] | None:
        source = await session.get(Action, source_action_id)
        if (
            source is None
            or source.namespace != self.namespace
            or source.status != "succeeded"
            or source.account != str(author_id)
            or source.account not in self.settings.accounts
            or source.payload.get("kind") != "post"
            or source.payload.get("reply_to_post_id") is not None
            or source.payload.get("repost_of_post_id") is not None
        ):
            return None
        body = source.payload.get("text")
        if not isinstance(body, str) or not body.strip():
            return None
        outcomes = list(
            await session.scalars(
                select(Outcome)
                .where(Outcome.action_id == source.id, Outcome.kind == "accepted")
                .order_by(Outcome.created_at.desc())
                .limit(2)
            )
        )
        if (
            len(outcomes) != 1
            or outcomes[0].payload.get("external_id") != str(target_id)
            or outcomes[0].created_at < now - max_age
            or outcomes[0].created_at > now
        ):
            return None
        disabled = await session.scalar(
            select(RedAccountControl.disabled).where(
                RedAccountControl.scope == self.scope,
                RedAccountControl.account == source.account,
            )
        )
        if disabled:
            return None
        return source, body

    async def _npc_author_reviewed(self, session: Any, author_id: int) -> bool:
        """Require archived NPC provenance and no contradictory player label."""
        reviewed = await session.scalar(
            select(
                exists().where(
                    NPCPost.namespace == self.config.archive_namespace,
                    NPCPost.author_id == author_id,
                    NPCPost.npc_status == "npc",
                )
            )
        )
        player = await session.scalar(
            select(
                exists().where(
                    NPCPost.namespace == self.config.archive_namespace,
                    NPCPost.author_id == author_id,
                    NPCPost.npc_status == "player",
                )
            )
        )
        return bool(reviewed) and not bool(player)

    async def reserve_engagement(
        self,
        account: str,
        target_id: int,
        kind: EngagementKind,
        *,
        target_type: TargetType,
        author_id: int,
        source_action_id: UUID | None = None,
        body_hash: str | None = None,
        text: str = "",
        policy_version: str = ENGAGEMENT_STRATEGY_VERSION,
    ) -> UUID | None:
        """Atomically persist Decision, Action, and cross-version target reservation."""
        for value in (target_id, author_id):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError("Engagement identifiers must be positive integers")
        if kind not in {"like", "repost", "team_reply"} or target_type not in {"own", "npc"}:
            raise ValueError("Unsupported engagement kind or target type")
        if not policy_version.strip() or len(policy_version) > 64:
            raise ValueError("A bounded engagement policy version is required")
        if kind == "team_reply" and not is_reviewed_team_reply(text):
            return None
        if kind != "team_reply" and text:
            return None
        max_age = timedelta(hours=getattr(self.config, "engagement_max_age_hours", 2))
        if max_age <= timedelta(0):
            return None
        async with self.db.sessions.begin() as session:
            campaign = await session.scalar(
                select(RedCampaign).where(RedCampaign.scope == self.scope).with_for_update()
            )
            if campaign is None:
                return None
            now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            control = await session.get(Control, self.namespace)
            if (
                not getattr(self.config, "engagement_enabled", False)
                or control is None
                or control.paused
                or not campaign.starts_at <= now < campaign.ends_at
                or account not in self.settings.accounts
                or account not in campaign.accounts
                or author_id == int(account)
            ):
                return None
            disabled = await session.scalar(
                select(RedAccountControl.disabled).where(
                    RedAccountControl.scope == self.scope,
                    RedAccountControl.account == account,
                )
            )
            if disabled:
                return None
            pending = await session.scalar(
                select(func.count())
                .select_from(Action)
                .where(
                    Action.namespace == self.namespace,
                    Action.status.in_(("queued", "in_flight")),
                )
            )
            if int(pending or 0) >= self.config.max_pending_actions:
                return None
            source: Action | None = None
            saved_body_hash = body_hash
            if target_type == "own":
                if source_action_id is None or str(author_id) not in campaign.accounts:
                    return None
                own = await self._accepted_original(
                    session, source_action_id, target_id, author_id, now, max_age
                )
                if own is None:
                    return None
                source, own_body = own
                expected_hash = body_digest(own_body)
                if body_hash is None or body_hash != expected_hash:
                    return None
                saved_body_hash = expected_hash
            elif target_type == "npc":
                if kind == "team_reply" or source_action_id is not None:
                    return None
                if str(author_id) in campaign.accounts:
                    return None
                post = await session.get(NPCPost, (self.config.archive_namespace, target_id))
                if (
                    post is None
                    or post.author_id != author_id
                    or post.kind != "post"
                    or post.post is None
                    or not post.post.strip()
                    or post.time_stamp < now - max_age
                    or post.time_stamp > now
                    or not is_reviewed_yes_target(post.post)
                    or body_hash is None
                    or body_hash != body_digest(post.post)
                ):
                    return None
                if not await self._npc_author_reviewed(session, author_id):
                    return None
                saved_body_hash = body_digest(post.post)
            else:
                return None

            maximum = {
                "like": getattr(self.config, "engagement_likes_per_target", 3),
                "repost": getattr(self.config, "engagement_reposts_per_target", 1),
                "team_reply": getattr(self.config, "engagement_comments_per_target", 1),
            }[kind]
            count = await session.scalar(
                select(func.count())
                .select_from(EngagementReservation)
                .where(
                    EngagementReservation.scope == self.scope,
                    EngagementReservation.target_id == target_id,
                    EngagementReservation.kind == kind,
                )
            )
            duplicate = await session.scalar(
                select(EngagementReservation.id).where(
                    EngagementReservation.scope == self.scope,
                    EngagementReservation.account == account,
                    EngagementReservation.target_id == target_id,
                    EngagementReservation.kind == kind,
                )
            )
            if duplicate is not None or int(count or 0) >= maximum:
                return None

            hourly_reservations = await session.scalar(
                select(func.count())
                .select_from(EngagementReservation)
                .where(
                    EngagementReservation.scope == self.scope,
                    EngagementReservation.created_at >= now - timedelta(hours=1),
                )
            )
            if int(hourly_reservations or 0) >= getattr(
                self.config, "engagement_max_per_hour", 100
            ):
                return None

            # Keep the existing campaign total, per-hour usage ledger, and pause
            # controls authoritative for these new actions as well.
            attempt_count = await session.scalar(
                select(func.count())
                .select_from(UsageEvent)
                .where(UsageEvent.namespace == self.namespace, UsageEvent.kind == "action_attempt")
            )
            if int(attempt_count or 0) >= campaign.max_attempts:
                return None

            if source is not None:
                source_decision = await session.get(Decision, source.decision_id)
                if source_decision is None:
                    return None
                observation_id = source_decision.observation_id
            else:
                observation = await session.scalar(
                    select(Observation).where(
                        Observation.namespace == self.namespace,
                        Observation.external_id == str(target_id),
                    )
                )
                if observation is None:
                    post = await session.get(NPCPost, (self.config.archive_namespace, target_id))
                    if post is None or post.post is None:
                        return None
                    observation = Observation(
                        namespace=self.namespace,
                        external_id=str(target_id),
                        payload={
                            "external_id": str(target_id),
                            "text": post.post,
                            "source": "social",
                            "metadata": {
                                "post_id": target_id,
                                "author_id": author_id,
                                "kind": "post",
                                "created_at": post.time_stamp.isoformat(),
                            },
                        },
                    )
                    session.add(observation)
                    await session.flush()
                observation_id = observation.id

            key = self.key(f"engagement:{account}:{target_id}:{kind}")
            draft = Draft(kind=kind, target_id=str(target_id), text=text)
            decision = Decision(
                namespace=self.namespace,
                key=key,
                observation_id=observation_id,
                account=account,
                strategy_version=policy_version,
                prompt_version=ENGAGEMENT_STRATEGY_VERSION,
                rationale="Reviewed finite engagement with durable source authority",
            )
            session.add(decision)
            await session.flush()
            action_id = uuid4()
            action = Action(
                id=action_id,
                namespace=self.namespace,
                decision_id=decision.id,
                key=key,
                account=account,
                payload=draft.model_dump(),
            )
            session.add(action)
            session.add(
                EngagementReservation(
                    scope=self.scope,
                    account=account,
                    target_id=target_id,
                    kind=kind,
                    author_id=author_id,
                    target_type=target_type,
                    action_id=action_id,
                    source_action_id=source_action_id,
                    body_hash=saved_body_hash,
                    root_id=target_id,
                    policy_version=policy_version,
                )
            )
            return action_id

    async def authority_for_action(self, action_id: UUID) -> Any | None:
        """Reload and validate persisted proof for a claimed action only."""
        from botfluencer.red.engagement_executor import EngagementAuthority

        async with self.db.sessions() as session:
            reservation = await session.scalar(
                select(EngagementReservation).where(
                    EngagementReservation.scope == self.scope,
                    EngagementReservation.action_id == action_id,
                )
            )
            action = await session.get(Action, action_id)
            if (
                reservation is None
                or action is None
                or action.namespace != self.namespace
                or action.status not in {"in_flight", "unknown"}
                or action.attempts < 1
                or action.account != reservation.account
                or action.account not in self.settings.accounts
                or action.payload.get("kind") != reservation.kind
                or action.payload.get("target_id") != str(reservation.target_id)
            ):
                return None
            campaign = await session.get(RedCampaign, self.scope)
            if (
                campaign is None
                or action.account not in campaign.accounts
                or action.account == str(reservation.author_id)
                or reservation.target_type == "npc"
                and str(reservation.author_id) in campaign.accounts
            ):
                return None
            disabled_actor = await session.scalar(
                select(RedAccountControl.disabled).where(
                    RedAccountControl.scope == self.scope,
                    RedAccountControl.account == action.account,
                )
            )
            if disabled_actor:
                return None
            now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            if not campaign.starts_at <= now < campaign.ends_at:
                return None
            max_age = timedelta(hours=getattr(self.config, "engagement_max_age_hours", 2))
            saved_hash = reservation.body_hash
            if reservation.target_type == "own":
                if (
                    reservation.source_action_id is None
                    or str(reservation.author_id) not in campaign.accounts
                ):
                    return None
                own = await self._accepted_original(
                    session,
                    reservation.source_action_id,
                    reservation.target_id,
                    reservation.author_id,
                    now,
                    max_age,
                )
                if own is None or body_digest(own[1]) != saved_hash:
                    return None
            else:
                if reservation.source_action_id is not None:
                    return None
                post = await session.get(
                    NPCPost, (self.config.archive_namespace, reservation.target_id)
                )
                if (
                    post is None
                    or post.author_id != reservation.author_id
                    or post.kind != "post"
                    or post.post is None
                    or post.time_stamp < now - max_age
                    or post.time_stamp > now
                    or not is_reviewed_yes_target(post.post)
                    or body_digest(post.post) != reservation.body_hash
                    or not await self._npc_author_reviewed(session, reservation.author_id)
                ):
                    return None
                saved_hash = body_digest(post.post)
            if reservation.kind == "team_reply":
                if (
                    reservation.target_type != "own"
                    or reservation.source_action_id is None
                    or not is_reviewed_team_reply(action.payload.get("text", ""))
                ):
                    return None
            return EngagementAuthority(
                action_id=action_id,
                account=action.account,
                target_id=str(reservation.target_id),
                kind=cast(EngagementKind, reservation.kind),
                author_id=reservation.author_id,
                target_type=cast(TargetType, reservation.target_type),
                source_action_id=reservation.source_action_id,
                body_hash=saved_hash,
                root_id=reservation.root_id,
            )

    async def authorize_window(self, end: datetime, reason: str) -> dict[str, Any]:
        """Require a stopped owner and explicit operator authorization to renew."""
        from botfluencer.red.mandate import authorize_window

        return await authorize_window(self, end=end, reason=reason)
