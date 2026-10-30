"""Red policy transactions layered on the existing execution ledger."""

import hashlib
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import String, exists, func, or_, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from botfluencer.domain import ActionIntent, Draft, ObservationInput
from botfluencer.red.config import RedSettings
from botfluencer.red.conversation import AcceptedParent, Continuation
from botfluencer.red.policy import TOPIC_TERMS
from botfluencer.storage.models import (
    Action,
    Decision,
    NPCPost,
    Observation,
    Outcome,
    RedAccountControl,
    RedCampaign,
    RedCampaignRevision,
    RedTarget,
    UsageEvent,
)
from botfluencer.storage.repository import Repository

STRATEGY_VERSION = "red-editorial-v2"
PROMPT_VERSION = "curated-yes-v1"
DISPATCHER = "red-dispatcher"


class RedStore(Repository):
    config: RedSettings
    scope: str

    async def historical_accounts(self) -> set[str]:
        """Return every account ever admitted to this team's campaign."""
        async with self.db.sessions() as session:
            campaign = await session.get(RedCampaign, self.scope)
            if campaign is None:
                raise ValueError("An existing campaign is required")
            return set(campaign.accounts)

    async def disabled_accounts(self) -> set[str]:
        async with self.db.sessions() as session:
            return set(
                await session.scalars(
                    select(RedAccountControl.account).where(
                        RedAccountControl.scope == self.scope, RedAccountControl.disabled.is_(True)
                    )
                )
            )

    async def active_accounts(self) -> list[str]:
        disabled = await self.disabled_accounts()
        return [account for account in self.settings.accounts if account not in disabled]

    async def quarantine(self, account: str, reason: str) -> None:
        if account not in self.settings.accounts or reason not in {
            "authentication_rejected",
            "session_expired",
            "operator_disabled",
            "server_banned",
        }:
            raise ValueError("Use a configured account and a sanitized eligibility reason")
        async with self.db.sessions.begin() as session:
            # Match planning/amendment/claim lock order. A planner that finishes
            # first leaves queued work for this transaction to fail; one that
            # follows quarantine sees the disabled control before reserving work.
            await session.scalar(
                select(RedCampaign).where(RedCampaign.scope == self.scope).with_for_update()
            )
            await session.execute(
                insert(RedAccountControl)
                .values(scope=self.scope, account=account, disabled=True, reason=reason)
                .on_conflict_do_update(
                    index_elements=["scope", "account"],
                    set_={"disabled": True, "reason": reason, "updated_at": func.now()},
                )
            )
            queued = await session.scalars(
                select(Action)
                .where(
                    Action.namespace == self.namespace,
                    Action.account == account,
                    Action.status == "queued",
                )
                .with_for_update()
            )
            for action in queued:
                action.status = "failed"
                session.add(
                    Outcome(
                        action_id=action.id,
                        attempt=action.attempts,
                        kind="account_disabled",
                        payload={"reason": reason},
                    )
                )

    async def amend(
        self, reason: str, *, verified_accounts: set[str] | None = None
    ) -> dict[str, int]:
        if not reason.strip() or len(reason) > 500:
            raise ValueError("An explicit bounded revision reason is required")
        async with self.db.sessions.begin() as session:
            campaign = await session.scalar(
                select(RedCampaign).where(RedCampaign.scope == self.scope).with_for_update()
            )
            control = await self._control(session)
            if campaign is None or not control.paused:
                raise ValueError("Campaign must exist and be paused before expansion")
            immutable = dict(
                namespace=self.namespace,
                starts_at=self.config.starts_at,
                ends_at=self.config.ends_at,
                archive_namespace=self.config.archive_namespace,
            )
            if any(getattr(campaign, k) != v for k, v in immutable.items()):
                raise ValueError("Expansion must preserve namespace, window and archive")
            now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            if now >= campaign.ends_at:
                raise ValueError("The campaign deadline has passed")
            previous = set(campaign.accounts)
            configured = set(self.settings.accounts)
            disabled = set(
                await session.scalars(
                    select(RedAccountControl.account).where(
                        RedAccountControl.scope == self.scope, RedAccountControl.disabled.is_(True)
                    )
                )
            )
            if not previous - configured <= disabled:
                raise ValueError("Omitted historical accounts must be durably disabled")
            if not configured - previous <= set(verified_accounts or ()):
                raise ValueError("Every new account must have a verified account and team identity")
            if self.config.max_attempts < campaign.max_attempts:
                raise ValueError("Expansion cannot lower the recorded cap")
            accounts = sorted(previous | configured)
            in_flight = await session.scalar(
                select(Action.id)
                .where(Action.namespace == self.namespace, Action.status == "in_flight")
                .limit(1)
            )
            if in_flight is not None:
                raise ValueError("Drain in-flight actions before expanding")
            if accounts == campaign.accounts and self.config.max_attempts == campaign.max_attempts:
                return {"accounts": len(accounts), "max_attempts": campaign.max_attempts}
            session.add(
                RedCampaignRevision(
                    scope=self.scope,
                    previous_accounts=campaign.accounts,
                    accounts=accounts,
                    previous_max_attempts=campaign.max_attempts,
                    max_attempts=self.config.max_attempts,
                    reason=reason,
                )
            )
            campaign.accounts = accounts
            campaign.max_attempts = self.config.max_attempts
            return {"accounts": len(accounts), "max_attempts": campaign.max_attempts}

    async def next_account(self) -> str:
        accounts = await self.active_accounts()
        if not accounts:
            raise RuntimeError("No eligible publishing accounts remain")
        async with self.db.sessions() as session:
            rows = await session.execute(
                select(UsageEvent.account, func.max(UsageEvent.created_at))
                .where(UsageEvent.namespace == self.namespace, UsageEvent.kind == "action_attempt")
                .group_by(UsageEvent.account)
            )
            used = {account: stamp for account, stamp in rows.all()}
            reserved = await session.execute(
                select(Action.account, func.max(Action.created_at))
                .where(Action.namespace == self.namespace)
                .group_by(Action.account)
            )
            for account, stamp in reserved.all():
                used[account] = max(used.get(account, self.config.starts_at), stamp)
            return min(accounts, key=lambda a: (used.get(a, self.config.starts_at), a))

    async def planning_preference(self) -> tuple[bool, str | None]:
        """Read original cadence and the last durable planning turn together."""
        recent_original = exists().where(
            Action.namespace == self.namespace,
            Action.payload["kind"].as_string() == "post",
            Action.created_at > func.now() - timedelta(seconds=self.config.post_interval_seconds),
        )
        latest_kind = (
            select(Action.payload["kind"].as_string())
            .where(Action.namespace == self.namespace)
            .order_by(Action.created_at.desc(), Action.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        async with self.db.sessions() as session:
            row = (await session.execute(select(recent_original, latest_kind))).one()
            return not bool(row[0]), row[1]

    async def original_streak(self, limit: int = 3) -> int:
        """Count a bounded run of durable original planning turns across restarts."""
        if not 1 <= limit <= 3:
            raise ValueError("Original streak limit must be between one and three")
        async with self.db.sessions() as session:
            kinds = await session.scalars(
                select(Action.payload["kind"].as_string())
                .where(Action.namespace == self.namespace)
                .order_by(Action.created_at.desc(), Action.id.desc())
                .limit(limit)
            )
            streak = 0
            for kind in kinds:
                if kind != "post":
                    break
                streak += 1
            return streak

    async def original_due(self) -> bool:
        return (await self.planning_preference())[0]

    async def contact_available(self, author_id: int) -> bool:
        async with self.db.sessions() as session:
            return not bool(
                await session.scalar(
                    select(RedTarget.post_id)
                    .where(
                        RedTarget.scope == self.scope,
                        RedTarget.author_id == author_id,
                        RedTarget.created_at > func.now() - timedelta(hours=24),
                    )
                    .limit(1)
                )
            )

    async def initialize(self) -> None:
        await super().initialize()
        values = dict(
            scope=self.scope,
            namespace=self.namespace,
            starts_at=self.config.starts_at,
            ends_at=self.config.ends_at,
            max_attempts=self.config.max_attempts,
            archive_namespace=self.config.archive_namespace,
            accounts=sorted(self.settings.accounts),
        )
        async with self.db.sessions.begin() as session:
            await session.execute(insert(RedCampaign).values(**values).on_conflict_do_nothing())
            campaign = await session.scalar(
                select(RedCampaign).where(RedCampaign.scope == self.scope).with_for_update()
            )
            if campaign is None or any(
                getattr(campaign, key) != value
                for key, value in values.items()
                if key != "accounts"
            ):
                raise ValueError(
                    "Persisted campaign differs; deadline and mandate cannot be replaced"
                )
            configured = set(self.settings.accounts)
            historical = set(campaign.accounts)
            disabled = set(
                await session.scalars(
                    select(RedAccountControl.account).where(
                        RedAccountControl.scope == self.scope, RedAccountControl.disabled.is_(True)
                    )
                )
            )
            if not configured <= historical or not historical - configured <= disabled:
                raise ValueError("Roster changes require a paused, verified campaign amendment")

    async def npc_ids(self) -> frozenset[int]:
        own = {int(account) for account in await self.historical_accounts()}
        async with self.db.sessions() as session:
            rows = list(
                await session.scalars(
                    select(NPCPost.author_id)
                    .where(
                        NPCPost.namespace == self.config.archive_namespace,
                        NPCPost.npc_status == "npc",
                    )
                    .distinct()
                )
            )
            players = set(
                await session.scalars(
                    select(NPCPost.author_id)
                    .where(
                        NPCPost.namespace == self.config.archive_namespace,
                        NPCPost.npc_status == "player",
                    )
                    .distinct()
                )
            )
            return frozenset(set(rows) - players - own - {int(a) for a in self.settings.accounts})

    def key(self, external_id: str) -> str:
        return hashlib.sha256(f"{self.scope}:{self.namespace}:{external_id}".encode()).hexdigest()

    async def find_observation(self, external_id: str) -> Observation | None:
        async with self.db.sessions() as session:
            result = await session.scalar(
                select(Observation).where(
                    Observation.namespace == self.namespace, Observation.external_id == external_id
                )
            )
            return result

    async def unplanned(
        self,
        account: str,
        strategy_version: str,
        prompt_version: str,
        *,
        exclude_external_ids: frozenset[str] = frozenset(),
    ) -> Observation | None:
        if account != DISPATCHER:
            return await super().unplanned(account, strategy_version, prompt_version)
        async with self.db.sessions() as session:
            planned = exists().where(
                Decision.observation_id == Observation.id, Decision.account == DISPATCHER
            )
            result = await session.scalar(
                select(Observation)
                .where(
                    Observation.namespace == self.namespace,
                    # Originals use find_observation with their exact internal key.
                    Observation.payload["source"].as_string() == "social",
                    Observation.external_id.not_in(exclude_external_ids),
                    # Routing only: this flag cannot authorize a continuation.
                    ~Observation.payload["metadata"]["conversation_candidate"]
                    .as_boolean()
                    .is_(True),
                    ~planned,
                )
                .order_by(
                    # Prefer recent discovery without hiding older audit history.
                    (Observation.observed_at >= func.now() - timedelta(hours=4)).desc(),
                    Observation.observed_at,
                    Observation.id,
                )
                .limit(1)
            )
            return result

    async def candidates(self, npc_ids: frozenset[int]) -> list[ObservationInput]:
        """Discover archived IDs outside the player-flooded public feed window.

        The archive does not store thread roots. Leave those fields absent here;
        the executor must retrieve and verify the current post before any write.
        """
        async with self.db.sessions() as session:
            observed = exists().where(
                Observation.namespace == self.namespace,
                Observation.external_id == sql_cast(NPCPost.post_id, String),
            )
            contacted = exists().where(
                RedTarget.scope == self.scope,
                RedTarget.author_id == NPCPost.author_id,
                RedTarget.created_at > func.now() - timedelta(hours=24),
            )
            ascii_lower = func.translate(
                NPCPost.post, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
            )
            rows = await session.scalars(
                select(NPCPost)
                .where(
                    NPCPost.namespace == self.config.archive_namespace,
                    NPCPost.author_id.in_(npc_ids),
                    NPCPost.kind.in_(("post", "reply") if self.config.allow_threads else ("post",)),
                    NPCPost.post.is_not(None),
                    func.length(func.btrim(NPCPost.post, " \t\r\n")) > 0,
                    or_(
                        *(ascii_lower.contains(term) for term in TOPIC_TERMS),
                        # Database Unicode lowercasing can differ from Python's;
                        # retain all multibyte text for the authoritative policy check.
                        func.octet_length(NPCPost.post)
                        != func.char_length(sql_cast(NPCPost.post, String)),
                    ),
                    NPCPost.time_stamp >= func.now() - timedelta(hours=4),
                    NPCPost.time_stamp <= func.now(),
                    ~observed,
                    ~contacted,
                )
                .order_by(NPCPost.time_stamp.desc(), NPCPost.post_id.desc())
                .limit(8)
            )
            return [
                ObservationInput(
                    external_id=str(row.post_id),
                    text=row.post,
                    source="social",
                    metadata={
                        "post_id": row.post_id,
                        "author_id": row.author_id,
                        "kind": row.kind,
                        "created_at": row.time_stamp.isoformat(),
                    },
                )
                for row in rows
                if row.post and row.post.strip()
            ]

    async def continuation_candidates(self, npc_ids: frozenset[int]) -> list[ObservationInput]:
        """A bounded discovery lane; fresh ancestry and receipts authorize replies.

        Keep observed but unplanned rows eligible so contact cooldowns survive
        restart without permanently discarding an NPC response.
        """
        async with self.db.sessions() as session:
            planned = exists().where(
                Observation.namespace == self.namespace,
                Observation.external_id == sql_cast(NPCPost.post_id, String),
                Decision.observation_id == Observation.id,
                Decision.account == DISPATCHER,
            )
            reserved = exists().where(
                RedTarget.scope == self.scope, RedTarget.post_id == NPCPost.post_id
            )
            rows = await session.scalars(
                select(NPCPost)
                .where(
                    NPCPost.namespace == self.config.archive_namespace,
                    NPCPost.author_id.in_(npc_ids),
                    NPCPost.kind == "reply",
                    NPCPost.post.is_not(None),
                    func.length(func.btrim(NPCPost.post, " \t\r\n")) > 0,
                    NPCPost.time_stamp >= func.now() - timedelta(hours=4),
                    NPCPost.time_stamp <= func.now(),
                    ~planned,
                    ~reserved,
                )
                .order_by(NPCPost.time_stamp.desc(), NPCPost.post_id.desc())
                .limit(8)
            )
            return [
                ObservationInput(
                    external_id=str(row.post_id),
                    text=row.post,
                    source="social",
                    metadata={
                        "post_id": row.post_id,
                        "author_id": row.author_id,
                        "kind": row.kind,
                        "created_at": row.time_stamp.isoformat(),
                    },
                )
                for row in rows
                if row.post and row.post.strip()
            ]

    async def _accepted_parent(
        self, session: AsyncSession, external_id: str
    ) -> AcceptedParent | None:
        if not external_id.isascii() or not external_id.isdecimal() or external_id.startswith("0"):
            return None
        disabled = exists().where(
            RedAccountControl.scope == self.scope,
            RedAccountControl.account == Action.account,
            RedAccountControl.disabled.is_(True),
        )
        rows = (
            await session.execute(
                select(Action.id, Action.account)
                .join(Outcome, Outcome.action_id == Action.id)
                .where(
                    Action.namespace == self.namespace,
                    Action.status == "succeeded",
                    Action.account.in_(self.settings.accounts),
                    Outcome.kind == "accepted",
                    Outcome.payload["external_id"].as_string() == external_id,
                    ~disabled,
                )
                .distinct()
                .limit(2)
            )
        ).all()
        if len(rows) != 1:
            return None
        action_id, account = rows[0]
        return AcceptedParent(action_id=action_id, account=account, external_id=external_id)

    async def accepted_parent(self, external_id: int | str) -> AcceptedParent | None:
        async with self.db.sessions() as session:
            return await self._accepted_parent(session, str(external_id))

    async def _valid_continuation(
        self, session: AsyncSession, continuation: Continuation, account: str
    ) -> bool:
        if (
            not self.config.allow_threads
            or isinstance(continuation.root_id, bool)
            or continuation.root_id < 1
            or isinstance(continuation.npc_author_id, bool)
            or continuation.npc_author_id < 1
        ):
            return False
        campaign = await session.get(RedCampaign, self.scope)
        if campaign is None or str(continuation.npc_author_id) in campaign.accounts:
            return False
        reviewed = await session.scalar(
            select(
                exists().where(
                    NPCPost.namespace == self.config.archive_namespace,
                    NPCPost.author_id == continuation.npc_author_id,
                    NPCPost.npc_status == "npc",
                )
            )
        )
        conflicting = await session.scalar(
            select(
                exists().where(
                    NPCPost.namespace == self.config.archive_namespace,
                    NPCPost.author_id == continuation.npc_author_id,
                    NPCPost.npc_status == "player",
                )
            )
        )
        if not reviewed or conflicting:
            return False
        parent = await self._accepted_parent(session, continuation.parent_external_id)
        if (
            parent is None
            or parent.action_id != continuation.parent_action_id
            or parent.account != account
        ):
            return False
        action = await session.get(Action, parent.action_id)
        if action is None:
            return False
        if action.payload.get("kind") == "post":
            return str(continuation.root_id) == parent.external_id
        if action.payload.get("kind") != "reply":
            return False
        reservation = await session.scalar(
            select(RedTarget).where(
                RedTarget.scope == self.scope, RedTarget.action_id == parent.action_id
            )
        )
        # Historical ordinary replies have no saved root; the executor freshly
        # verifies that ancestry. Later continuation parents bind the root here.
        return reservation is not None and reservation.conversation_root_id in (
            None,
            continuation.root_id,
        )

    async def _continuation_available(
        self, session: AsyncSession, author_id: int, continuation: Continuation, now: datetime
    ) -> bool:
        if author_id != continuation.npc_author_id:
            return False
        base = (RedTarget.scope == self.scope, RedTarget.author_id == author_id)
        last_contact = await session.scalar(select(func.max(RedTarget.created_at)).where(*base))
        last_outcome = await session.scalar(
            select(func.max(Outcome.created_at))
            .join(RedTarget, RedTarget.action_id == Outcome.action_id)
            .where(*base)
        )
        if last_outcome is not None:
            last_contact = max(last_contact, last_outcome) if last_contact else last_outcome
        if last_contact is not None and last_contact > now - timedelta(minutes=5):
            return False
        turns = await session.scalar(
            select(func.count())
            .select_from(RedTarget)
            .where(
                *base,
                RedTarget.continuation_parent_action_id.is_not(None),
                RedTarget.created_at > now - timedelta(hours=24),
            )
        )
        root_turns = await session.scalar(
            select(func.count())
            .select_from(RedTarget)
            .where(
                *base,
                RedTarget.continuation_parent_action_id.is_not(None),
                RedTarget.conversation_root_id == continuation.root_id,
            )
        )
        blocked = await session.scalar(
            select(RedTarget.post_id)
            .join(Action, Action.id == RedTarget.action_id)
            .where(
                RedTarget.scope == self.scope,
                RedTarget.conversation_root_id == continuation.root_id,
                RedTarget.continuation_parent_action_id.is_not(None),
                Action.status.in_(("queued", "in_flight", "unknown")),
            )
            .limit(1)
        )
        return (turns or 0) < 2 and (root_turns or 0) < 2 and blocked is None

    async def continuation_available(self, author_id: int, continuation: Continuation) -> bool:
        async with self.db.sessions() as session:
            now = cast(datetime, await session.scalar(select(func.now())))
            parent = await self._accepted_parent(session, continuation.parent_external_id)
            return (
                parent is not None
                and await self._valid_continuation(session, continuation, parent.account)
                and await self._continuation_available(session, author_id, continuation, now)
            )

    async def continuation_for_action(self, action_id: UUID) -> Continuation | None:
        """Recover authority from the reservation, never observation metadata.

        None denotes ordinary intent. Invalid saved continuation authority raises
        so callers cannot silently downgrade it into an ordinary thread reply.
        """
        async with self.db.sessions() as session:
            reservation = await session.scalar(
                select(RedTarget).where(
                    RedTarget.scope == self.scope, RedTarget.action_id == action_id
                )
            )
            if reservation is None or reservation.continuation_parent_action_id is None:
                return None
            action = await session.get(Action, action_id)
            receipts = list(
                await session.scalars(
                    select(Outcome.payload["external_id"].as_string())
                    .where(
                        Outcome.action_id == reservation.continuation_parent_action_id,
                        Outcome.kind == "accepted",
                    )
                    .distinct()
                    .limit(2)
                )
            )
            if (
                action is None
                or action.namespace != self.namespace
                or reservation.conversation_root_id is None
                or len(receipts) != 1
                or not isinstance(receipts[0], str)
            ):
                raise ValueError("Invalid durable continuation authority")
            continuation = Continuation(
                root_id=reservation.conversation_root_id,
                parent_action_id=reservation.continuation_parent_action_id,
                parent_external_id=receipts[0],
                npc_author_id=reservation.author_id,
            )
            if (
                action.payload.get("kind") != "reply"
                or action.payload.get("target_id") != str(reservation.post_id)
                or not await self._valid_continuation(session, continuation, action.account)
            ):
                raise ValueError("Invalid durable continuation authority")
            return continuation

    async def skip(self, observation_id: UUID, external_id: str, reason: str) -> None:
        async with self.db.sessions.begin() as session:
            await session.execute(
                insert(Decision)
                .values(
                    namespace=self.namespace,
                    key=self.key(external_id),
                    observation_id=observation_id,
                    account=DISPATCHER,
                    strategy_version=STRATEGY_VERSION,
                    prompt_version=PROMPT_VERSION,
                    rationale=reason,
                )
                .on_conflict_do_nothing(index_elements=["namespace", "key"])
            )

    async def plan_reply(
        self,
        observation_id: UUID,
        author_id: int,
        account: str,
        draft: Draft,
        *,
        prompt_version: str = PROMPT_VERSION,
        continuation: Continuation | None = None,
    ) -> bool:
        async with self.db.sessions.begin() as session:
            campaign = await session.scalar(
                select(RedCampaign).where(RedCampaign.scope == self.scope).with_for_update()
            )
            if campaign is None:
                raise RuntimeError("Campaign is not initialized")
            disabled = await session.scalar(
                select(RedAccountControl.disabled).where(
                    RedAccountControl.scope == self.scope, RedAccountControl.account == account
                )
            )
            if (
                disabled
                or account not in self.settings.accounts
                or account not in campaign.accounts
            ):
                return False
            control = await self._control(session)
            if control.paused:
                return False
            now = cast(datetime, await session.scalar(select(func.now())))
            if not campaign.starts_at <= now < campaign.ends_at:
                return False
            if continuation is not None:
                if draft.kind != "reply" or not await self._valid_continuation(
                    session, continuation, account
                ):
                    return False
                if not await self._continuation_available(session, author_id, continuation, now):
                    return False
            # Cross-account and cross-version reservation includes rejected/unknown writes.
            seen = (
                await session.get(RedTarget, (self.scope, int(draft.target_id)))
                if draft.kind == "reply"
                else None
            )
            recent = (
                await session.scalar(
                    select(RedTarget.post_id)
                    .where(
                        RedTarget.scope == self.scope,
                        RedTarget.author_id == author_id,
                        RedTarget.created_at > now - timedelta(hours=24),
                    )
                    .limit(1)
                )
                if draft.kind == "reply" and continuation is None
                else None
            )
            total = await session.scalar(
                select(func.count()).select_from(RedTarget).where(RedTarget.scope == self.scope)
            )
            if seen is not None or recent is not None or (total or 0) >= campaign.max_attempts:
                return False
            existing = await session.scalar(
                select(Action.id).where(
                    Action.namespace == self.namespace, Action.key == self.key(draft.target_id)
                )
            )
            decided = await session.scalar(
                select(Decision.id).where(
                    Decision.namespace == self.namespace, Decision.key == self.key(draft.target_id)
                )
            )
            if existing is not None or decided is not None:
                return False
            decision = Decision(
                namespace=self.namespace,
                key=self.key(draft.target_id),
                observation_id=observation_id,
                account=DISPATCHER,
                strategy_version=STRATEGY_VERSION,
                prompt_version=prompt_version,
                rationale=(
                    "Reviewed NPC conversation continuation with durable parent receipt"
                    if continuation
                    else "Curated simulation reply to reviewed NPC; one team contact per 24h"
                ),
            )
            session.add(decision)
            await session.flush()
            action = Action(
                namespace=self.namespace,
                decision_id=decision.id,
                key=decision.key,
                account=account,
                payload=draft.model_dump(),
            )
            session.add(action)
            await session.flush()
            if draft.kind == "reply":
                session.add(
                    RedTarget(
                        scope=self.scope,
                        post_id=int(draft.target_id),
                        author_id=author_id,
                        action_id=action.id,
                        conversation_root_id=continuation.root_id if continuation else None,
                        continuation_parent_action_id=(
                            continuation.parent_action_id if continuation else None
                        ),
                    )
                )
            return True

    async def claim(self, allowed_accounts: list[str] | None = None) -> ActionIntent | None:
        eligible = await self.active_accounts()
        if allowed_accounts is not None:
            eligible = [account for account in eligible if account in allowed_accounts]
        if not eligible:
            return None
        async with self.db.sessions.begin() as session:
            campaign = await session.scalar(
                select(RedCampaign).where(RedCampaign.scope == self.scope).with_for_update()
            )
            now = cast(datetime, await session.scalar(select(func.now())))
            if campaign is None or not campaign.starts_at <= now < campaign.ends_at:
                return None
            if await self._used_total() >= campaign.max_attempts:
                return None
            return await super().claim(eligible)

    async def has_pending(self) -> bool:
        async with self.db.sessions() as session:
            return bool(
                await session.scalar(
                    select(
                        exists().where(
                            Action.namespace == self.namespace,
                            Action.status.in_(("queued", "in_flight")),
                        )
                    )
                )
            )

    async def pending_count(self) -> int:
        async with self.db.sessions() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(Action)
                    .where(
                        Action.namespace == self.namespace,
                        Action.status.in_(("queued", "in_flight")),
                    )
                )
                or 0
            )

    async def _used_total(self) -> int:
        from botfluencer.storage.models import UsageEvent

        async with self.db.sessions() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(UsageEvent)
                    .where(
                        UsageEvent.namespace == self.namespace, UsageEvent.kind == "action_attempt"
                    )
                )
                or 0
            )
