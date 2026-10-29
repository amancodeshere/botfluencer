"""Red policy transactions layered on the existing execution ledger."""

import hashlib
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from botfluencer.domain import ActionIntent, Draft
from botfluencer.red.config import RedSettings
from botfluencer.storage.models import Action, Decision, NPCPost, RedCampaign, RedTarget
from botfluencer.storage.repository import Repository

STRATEGY_VERSION = "npc-top-level-reply-v1"
PROMPT_VERSION = "curated-yes-v1"
DISPATCHER = "red-dispatcher"


class RedStore(Repository):
    config: RedSettings
    scope: str

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
            campaign = await session.get(RedCampaign, self.scope)
            if campaign is None or any(
                getattr(campaign, key) != value for key, value in values.items()
            ):
                raise ValueError(
                    "Persisted campaign differs; deadline and mandate cannot be replaced"
                )

    async def npc_ids(self) -> frozenset[int]:
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
            return frozenset(set(rows) - players - {int(a) for a in self.settings.accounts})

    def key(self, external_id: str) -> str:
        return hashlib.sha256(f"{self.scope}:{self.namespace}:{external_id}".encode()).hexdigest()

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
        self, observation_id: UUID, author_id: int, account: str, draft: Draft
    ) -> bool:
        async with self.db.sessions.begin() as session:
            campaign = await session.scalar(
                select(RedCampaign).where(RedCampaign.scope == self.scope).with_for_update()
            )
            if campaign is None:
                raise RuntimeError("Campaign is not initialized")
            now = cast(datetime, await session.scalar(select(func.now())))
            if not campaign.starts_at <= now < campaign.ends_at:
                return False
            # Cross-account and cross-version reservation includes rejected/unknown writes.
            seen = await session.get(RedTarget, (self.scope, int(draft.target_id)))
            recent = await session.scalar(
                select(RedTarget.post_id)
                .where(
                    RedTarget.scope == self.scope,
                    RedTarget.author_id == author_id,
                    RedTarget.created_at > now - timedelta(hours=24),
                )
                .limit(1)
            )
            total = await session.scalar(
                select(func.count()).select_from(RedTarget).where(RedTarget.scope == self.scope)
            )
            if seen is not None or recent is not None or (total or 0) >= campaign.max_attempts:
                return False
            decision = Decision(
                namespace=self.namespace,
                key=self.key(draft.target_id),
                observation_id=observation_id,
                account=DISPATCHER,
                strategy_version=STRATEGY_VERSION,
                prompt_version=PROMPT_VERSION,
                rationale="Curated simulation reply to reviewed NPC; one team contact per 24h",
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
            session.add(
                RedTarget(
                    scope=self.scope,
                    post_id=int(draft.target_id),
                    author_id=author_id,
                    action_id=action.id,
                )
            )
            return True

    async def claim(self, allowed_accounts: list[str] | None = None) -> ActionIntent | None:
        async with self.db.sessions.begin() as session:
            campaign = await session.scalar(
                select(RedCampaign).where(RedCampaign.scope == self.scope).with_for_update()
            )
            now = cast(datetime, await session.scalar(select(func.now())))
            if campaign is None or not campaign.starts_at <= now < campaign.ends_at:
                return None
            if await self._used_total() >= campaign.max_attempts:
                return None
            return await super().claim(allowed_accounts)

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
