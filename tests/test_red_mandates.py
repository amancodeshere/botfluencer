"""Postgres recovery tests for explicit red campaign mandate renewal."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from botfluencer.red.config import RedSettings
from botfluencer.red.engagement_store import EngagementStore
from botfluencer.storage.database import LeadershipBusy
from botfluencer.storage.models import (
    Action,
    Decision,
    Observation,
    Outcome,
    RedCampaign,
    RedMandateRevision,
)
from botfluencer.storage.repository import Repository


def expired_config(namespace: str, *, max_attempts: int = 17) -> RedSettings:
    now = datetime.now(UTC)
    return RedSettings(
        _env_file=None,
        expected_team_id=99,
        starts_at=now - timedelta(hours=25),
        ends_at=now - timedelta(hours=1),
        max_attempts=max_attempts,
        archive_namespace="synthetic-archive",
        sessions_json='[{"account_id":10,"token":"synthetic-token"}]',
    )


async def expired_store(repo: Repository, *, max_attempts: int = 17) -> EngagementStore:
    repo.settings.accounts = ["10"]
    store = EngagementStore(repo.db)
    store.scope = "mandate-" + uuid4().hex
    store.config = expired_config(repo.namespace, max_attempts=max_attempts)
    await store.initialize()
    return store


async def add_action(store: EngagementStore, *, status: str, suffix: str) -> UUID:
    identity = uuid4()
    async with store.db.sessions.begin() as session:
        observation = Observation(
            namespace=store.namespace,
            external_id="mandate-" + suffix,
            payload={"text": "synthetic"},
        )
        session.add(observation)
        await session.flush()
        decision = Decision(
            namespace=store.namespace,
            key="mandate-decision-" + suffix,
            observation_id=observation.id,
            account="10",
            strategy_version="synthetic-v1",
            prompt_version="synthetic-v1",
            rationale="synthetic mandate test",
        )
        session.add(decision)
        await session.flush()
        session.add(
            Action(
                id=identity,
                namespace=store.namespace,
                decision_id=decision.id,
                key="mandate-action-" + suffix,
                account="10",
                payload={"kind": "like", "target_id": "123", "text": ""},
                status=status,
                attempts=1,
            )
        )
    return identity


@pytest.mark.integration
async def test_renewal_requires_paused_expired_campaign_and_future_window(repo: Repository) -> None:
    store = await expired_store(repo)
    end = datetime.now(UTC) + timedelta(hours=12)
    with pytest.raises(ValueError, match="Pause"):
        await store.authorize_window(end=end, reason="synthetic reviewed renewal")
    await store.set_paused(True)
    with pytest.raises(ValueError, match="future and no more than 48 hours"):
        await store.authorize_window(end=datetime.now(UTC) - timedelta(minutes=1), reason="expired")
    with pytest.raises(ValueError, match="future and no more than 48 hours"):
        await store.authorize_window(end=datetime.now(UTC) + timedelta(hours=49), reason="too long")
    result = await store.authorize_window(end=end, reason="synthetic reviewed renewal")
    assert result["namespace"] == repo.namespace
    assert result["max_attempts"] == 17
    assert result["ends_at"] == end
    async with repo.db.sessions() as session:
        campaign = await session.get(RedCampaign, store.scope)
        revision = await session.get(RedMandateRevision, result["revision_id"])
    assert campaign is not None and campaign.max_attempts == 17
    assert campaign.namespace == repo.namespace
    assert campaign.archive_namespace == "synthetic-archive"
    assert revision is not None
    assert revision.previous_ends_at == store.config.ends_at
    assert revision.previous_max_attempts == revision.max_attempts == 17
    assert revision.reason == "synthetic reviewed renewal"


@pytest.mark.integration
async def test_renewal_fails_if_worker_still_holds_namespace_advisory_lock(
    repo: Repository,
) -> None:
    store = await expired_store(repo)
    await store.set_paused(True)
    end = datetime.now(UTC) + timedelta(hours=2)
    async with repo.db.leadership(store.namespace):
        with pytest.raises(LeadershipBusy):
            await store.authorize_window(end=end, reason="synthetic lock check")
    async with repo.db.sessions() as session:
        assert (
            await session.scalar(
                select(RedMandateRevision.id).where(RedMandateRevision.scope == store.scope)
            )
            is None
        )


@pytest.mark.integration
async def test_renewal_marks_queued_failed_and_preserves_unknown_history(repo: Repository) -> None:
    store = await expired_store(repo)
    await store.set_paused(True)
    queued_id = await add_action(store, status="queued", suffix="queued")
    unknown_id = await add_action(store, status="unknown", suffix="unknown")
    end = datetime.now(UTC) + timedelta(hours=4)
    result = await store.authorize_window(end=end, reason="synthetic recovery")
    assert result["expired_queued_actions"] == 1
    async with repo.db.sessions() as session:
        queued = await session.get(Action, queued_id)
        unknown = await session.get(Action, unknown_id)
        outcomes = list(
            await session.scalars(select(Outcome).where(Outcome.action_id == queued_id))
        )
        unknown_outcomes = list(
            await session.scalars(select(Outcome).where(Outcome.action_id == unknown_id))
        )
    assert queued is not None and queued.status == "failed"
    assert len(outcomes) == 1 and outcomes[0].kind == "mandate_expired"
    assert unknown is not None and unknown.status == "unknown"
    assert unknown_outcomes == []


@pytest.mark.integration
async def test_inflight_action_aborts_renewal_without_partial_updates(repo: Repository) -> None:
    store = await expired_store(repo)
    await store.set_paused(True)
    queued_id = await add_action(store, status="queued", suffix="rollback-queued")
    await add_action(store, status="in_flight", suffix="rollback-flight")
    with pytest.raises(ValueError, match="in-flight"):
        await store.authorize_window(
            end=datetime.now(UTC) + timedelta(hours=3), reason="synthetic drain check"
        )
    async with repo.db.sessions() as session:
        queued = await session.get(Action, queued_id)
        revisions = list(
            await session.scalars(
                select(RedMandateRevision).where(RedMandateRevision.scope == store.scope)
            )
        )
        outcomes = list(
            await session.scalars(select(Outcome).where(Outcome.action_id == queued_id))
        )
    assert queued is not None and queued.status == "queued"
    assert revisions == []
    assert outcomes == []


@pytest.mark.integration
async def test_unknown_engagement_reconciliation_batches_are_disjoint_and_restart_safe(
    repo: Repository,
) -> None:
    store = await expired_store(repo)
    action_ids = [
        await add_action(store, status="unknown", suffix=f"reconcile-{index}") for index in range(6)
    ]

    first_batch = await store.unknown_engagements()
    second_batch = await store.unknown_engagements()
    assert len(first_batch) == 4
    assert len(second_batch) == 2
    first_ids = {intent.id for intent in first_batch}
    second_ids = {intent.id for intent in second_batch}
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == set(action_ids)
    assert await store.unknown_engagements() == []

    restarted = EngagementStore(repo.db)
    restarted.scope = store.scope
    restarted.config = store.config
    await restarted.initialize()
    assert await restarted.unknown_engagements() == []

    async with repo.db.sessions() as session:
        actions = [await session.get(Action, action_id) for action_id in action_ids]
    assert all(action is not None for action in actions)
    assert {action.status for action in actions if action is not None} == {"unknown"}
    assert {action.attempts for action in actions if action is not None} == {1}
    assert all(
        action.next_attempt_at > datetime.now(UTC) for action in actions if action is not None
    )
