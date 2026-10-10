"""Transaction boundaries for the durable ledger and budget reservations."""

from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from botfluencer.config import Settings
from botfluencer.domain import ActionIntent, ActionStatus, Draft, ObservationInput, Receipt
from botfluencer.storage.database import Database
from botfluencer.storage.models import (
    Action,
    Control,
    Decision,
    MockReceipt,
    Observation,
    Outcome,
    UsageEvent,
)


class Repository:
    def __init__(self, database: Database) -> None:
        self.db = database
        self.settings: Settings = database.settings
        self.namespace = self.settings.namespace

    async def initialize(self) -> None:
        async with self.db.sessions.begin() as session:
            await session.execute(
                insert(Control)
                .values(namespace=self.namespace, mode=self.settings.mode)
                .on_conflict_do_nothing()
            )
            control = await session.get(Control, self.namespace)
            if control is None or control.mode != self.settings.mode:
                raise RuntimeError("Namespace mode differs; use a separate namespace for this mode")

    async def heartbeat(self, worker_id: UUID) -> None:
        async with self.db.sessions.begin() as session:
            await session.execute(
                update(Control)
                .where(Control.namespace == self.namespace)
                .values(heartbeat_at=func.now(), worker_id=worker_id)
            )

    async def set_paused(self, paused: bool) -> None:
        async with self.db.sessions.begin() as session:
            result = await session.scalar(
                update(Control)
                .where(Control.namespace == self.namespace)
                .values(paused=paused)
                .returning(Control.namespace)
            )
            if result is None:
                raise RuntimeError("Namespace does not exist; migrate and run it first")

    async def is_paused(self) -> bool:
        async with self.db.sessions() as session:
            control = await session.get(Control, self.namespace)
            return control is None or control.paused

    async def observe(self, inputs: list[ObservationInput]) -> None:
        async with self.db.sessions.begin() as session:
            for item in inputs:
                await session.execute(
                    insert(Observation)
                    .values(
                        namespace=self.namespace,
                        external_id=item.external_id,
                        payload=item.model_dump(),
                    )
                    .on_conflict_do_nothing(index_elements=["namespace", "external_id"])
                )

    async def unplanned(
        self, account: str, strategy_version: str, prompt_version: str
    ) -> Observation | None:
        async with self.db.sessions() as session:
            planned = exists().where(
                Decision.observation_id == Observation.id,
                Decision.account == account,
                Decision.strategy_version == strategy_version,
                Decision.prompt_version == prompt_version,
            )
            result = await session.scalar(
                select(Observation)
                .where(Observation.namespace == self.namespace, ~planned)
                .order_by(Observation.observed_at, Observation.id)
                .limit(1)
            )
            return result

    async def plan(
        self,
        observation_id: UUID,
        account: str,
        key: str,
        strategy_version: str,
        prompt_version: str,
        draft: Draft,
    ) -> UUID:
        async with self.db.sessions.begin() as session:
            observation = await session.get(Observation, observation_id)
            if observation is None or observation.namespace != self.namespace:
                raise ValueError("Observation must belong to this namespace")
            decision_id = await session.scalar(
                insert(Decision)
                .values(
                    namespace=self.namespace,
                    key=key,
                    observation_id=observation_id,
                    account=account,
                    strategy_version=strategy_version,
                    prompt_version=prompt_version,
                    rationale="Synthetic baseline for exercising the runtime only",
                )
                .on_conflict_do_nothing(index_elements=["namespace", "key"])
                .returning(Decision.id)
            )
            if decision_id is None:
                existing = await session.scalar(
                    select(Action.id).where(Action.namespace == self.namespace, Action.key == key)
                )
                if existing is None:
                    raise RuntimeError("Decision has no action; ledger invariant violated")
                return existing
            action_id = await session.scalar(
                insert(Action)
                .values(
                    namespace=self.namespace,
                    decision_id=decision_id,
                    key=key,
                    account=account,
                    payload=draft.model_dump(),
                )
                .returning(Action.id)
            )
            assert action_id is not None
            return action_id

    async def _control(self, session: AsyncSession) -> Control:
        control = await session.scalar(
            select(Control).where(Control.namespace == self.namespace).with_for_update()
        )
        if control is None:
            raise RuntimeError("Namespace is not initialized")
        return control

    async def _used(self, session: AsyncSession, kind: str, now: datetime) -> int:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(UsageEvent)
                .where(
                    UsageEvent.namespace == self.namespace,
                    UsageEvent.kind == kind,
                    UsageEvent.created_at > now - timedelta(hours=1),
                )
            )
            or 0
        )

    async def reserve_generation(self, account: str) -> bool:
        async with self.db.sessions.begin() as session:
            control = await self._control(session)
            now = cast(datetime, await session.scalar(select(func.now())))
            if control.paused or (
                await self._used(session, "llm_call", now) >= self.settings.max_llm_calls_per_hour
            ):
                return False
            session.add(UsageEvent(namespace=self.namespace, account=account, kind="llm_call"))
            return True

    async def claim(self) -> ActionIntent | None:
        """Reserve an attempt and its budget before any adapter side effect."""
        async with self.db.sessions.begin() as session:
            control = await self._control(session)
            now = cast(datetime, await session.scalar(select(func.now())))
            if control.paused or (
                await self._used(session, "action_attempt", now)
                >= self.settings.max_actions_per_hour
            ):
                return None
            actions = await session.scalars(
                select(Action)
                .where(
                    Action.namespace == self.namespace,
                    Action.status == ActionStatus.QUEUED,
                    Action.next_attempt_at <= now,
                    Action.account.in_(self.settings.accounts),
                )
                .order_by(Action.created_at, Action.id)
                .with_for_update(skip_locked=True)
            )
            for action in actions:
                if action.attempts >= self.settings.max_action_attempts:
                    action.status = ActionStatus.FAILED
                    continue
                last = await session.scalar(
                    select(func.max(UsageEvent.created_at)).where(
                        UsageEvent.namespace == self.namespace,
                        UsageEvent.account == action.account,
                        UsageEvent.kind == "action_attempt",
                    )
                )
                if (
                    last is not None
                    and (now - last).total_seconds() < self.settings.account_cooldown_seconds
                ):
                    continue
                action.attempts += 1
                action.status = ActionStatus.IN_FLIGHT
                session.add(
                    UsageEvent(
                        namespace=self.namespace, account=action.account, kind="action_attempt"
                    )
                )
                return self._intent(action)
            return None

    @staticmethod
    def _intent(action: Action) -> ActionIntent:
        return ActionIntent(
            id=action.id,
            key=action.key,
            account=action.account,
            draft=Draft.model_validate(action.payload),
            attempts=action.attempts,
        )

    async def finish(
        self,
        intent: ActionIntent,
        status: ActionStatus,
        kind: str,
        payload: dict[str, Any],
        delay_seconds: float = 0,
    ) -> None:
        async with self.db.sessions.begin() as session:
            action = await session.scalar(
                select(Action)
                .where(Action.id == intent.id, Action.namespace == self.namespace)
                .with_for_update()
            )
            if action is None:
                raise RuntimeError("Action not found")
            if action.status in {ActionStatus.SUCCEEDED, ActionStatus.FAILED}:
                return
            if (
                status == ActionStatus.QUEUED
                and action.attempts >= self.settings.max_action_attempts
            ):
                status = ActionStatus.FAILED
            action.status = status
            now = cast(datetime, await session.scalar(select(func.now())))
            action.next_attempt_at = now + timedelta(seconds=delay_seconds)
            session.add(
                Outcome(action_id=action.id, attempt=action.attempts, kind=kind, payload=payload)
            )

    async def recover(self) -> None:
        """A stopped process cannot know if an in-flight external call succeeded."""
        async with self.db.sessions.begin() as session:
            await session.execute(
                update(Action)
                .where(
                    Action.namespace == self.namespace,
                    Action.status == ActionStatus.IN_FLIGHT,
                )
                .values(status=ActionStatus.UNKNOWN)
            )

    async def unknown(self) -> list[ActionIntent]:
        async with self.db.sessions() as session:
            actions = await session.scalars(
                select(Action).where(
                    Action.namespace == self.namespace, Action.status == ActionStatus.UNKNOWN
                )
            )
            return [self._intent(action) for action in actions]

    async def mock_execute(self, intent: ActionIntent) -> Receipt:
        """Emulate durable, idempotent remote acceptance for recovery tests."""
        receipt = Receipt(external_id=f"mock-{intent.key}", details={"synthetic": True})
        async with self.db.sessions.begin() as session:
            await session.execute(
                insert(MockReceipt)
                .values(
                    namespace=self.namespace,
                    key=intent.key,
                    external_id=receipt.external_id,
                    payload=receipt.model_dump(),
                )
                .on_conflict_do_nothing(index_elements=["namespace", "key"])
            )
        return receipt

    async def mock_lookup(self, key: str) -> Receipt | None:
        async with self.db.sessions() as session:
            stored = await session.scalar(
                select(MockReceipt).where(
                    MockReceipt.namespace == self.namespace, MockReceipt.key == key
                )
            )
            return Receipt.model_validate(stored.payload) if stored is not None else None

    async def summary(self) -> dict[str, Any]:
        async with self.db.sessions() as session:
            control = await session.get(Control, self.namespace)
            if control is None:
                raise RuntimeError("Namespace is not initialized")
            counts = {}
            for model in (Observation, Decision, Action):
                counts[model.__tablename__] = await session.scalar(
                    select(func.count()).select_from(model).where(model.namespace == self.namespace)
                )
            counts["outcomes"] = await session.scalar(
                select(func.count())
                .select_from(Outcome)
                .join(Action, Outcome.action_id == Action.id)
                .where(Action.namespace == self.namespace)
            )
            status_rows = await session.execute(
                select(Action.status, func.count())
                .where(Action.namespace == self.namespace)
                .group_by(Action.status)
            )
            statuses = {row[0]: row[1] for row in status_rows}
            now = cast(datetime, await session.scalar(select(func.now())))
            return {
                "namespace": self.namespace,
                "mode": control.mode,
                "paused": control.paused,
                "heartbeat_at": control.heartbeat_at.isoformat() if control.heartbeat_at else None,
                "worker_id": str(control.worker_id) if control.worker_id else None,
                "counts": counts,
                "action_statuses": statuses,
                "action_attempts_last_hour": await self._used(session, "action_attempt", now),
                "llm_calls_last_hour": await self._used(session, "llm_call", now),
            }
