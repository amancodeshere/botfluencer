"""Batch persistence and quarantine/planning ordering without external API calls."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.dialects import postgresql

from botfluencer.domain import ActionStatus, Draft, ObservationInput
from botfluencer.red.config import RedSettings
from botfluencer.red.store import RedStore
from botfluencer.storage.models import Action, Decision, Observation, RedCampaign, RedTarget
from botfluencer.storage.repository import Repository


def recording_repository():
    session = SimpleNamespace(execute=AsyncMock())

    @asynccontextmanager
    async def transaction():
        yield session

    begin = Mock(side_effect=transaction)
    database = SimpleNamespace(
        settings=SimpleNamespace(namespace="synthetic-batch"),
        sessions=SimpleNamespace(begin=begin),
    )
    return Repository(database), session.execute, begin


async def test_empty_observation_batch_does_not_open_transaction() -> None:
    repository, execute, begin = recording_repository()
    await repository.observe([])
    begin.assert_not_called()
    execute.assert_not_awaited()


async def test_large_observation_batch_uses_bounded_inserts_and_keeps_first_duplicate() -> None:
    repository, execute, begin = recording_repository()
    items = [ObservationInput(external_id=str(i), text=f"Fixture {i}") for i in range(1201)]
    items.append(ObservationInput(external_id="0", text="Must not replace first payload"))
    await repository.observe(items)
    begin.assert_called_once()
    assert 1 < execute.await_count <= 4
    stored = {}
    for call in execute.await_args_list:
        params = call.args[0].compile(dialect=postgresql.dialect()).params
        identities = {key: value for key, value in params.items() if key.startswith("external_id")}
        assert 0 < len(identities) <= 500
        assert "ON CONFLICT" in str(call.args[0])
        for key, identity in identities.items():
            suffix = key.removeprefix("external_id")
            assert params["namespace" + suffix] == repository.namespace
            assert identity not in stored
            stored[identity] = params["payload" + suffix]
    assert len(stored) == 1201
    assert stored["0"] == items[0].model_dump(exclude_defaults=True)


@pytest.mark.integration
async def test_observation_batch_preserves_existing_payload_and_namespace(repo: Repository) -> None:
    first = ObservationInput(
        external_id="duplicate", text="First fixture", metadata={"first": True}
    )
    changed = first.model_copy(update={"text": "Later fixture", "metadata": {"later": True}})
    await repo.observe([first, changed])
    await repo.observe([changed, ObservationInput(external_id="new", text="New fixture")])
    other = Repository(repo.db)
    other.namespace = repo.namespace + "-other"
    await other.initialize()
    await other.observe([changed])
    async with repo.db.sessions() as session:
        rows = list(
            await session.scalars(
                select(Observation).where(
                    Observation.namespace.in_([repo.namespace, other.namespace])
                )
            )
        )
    assert len(rows) == 3
    payloads = {(row.namespace, row.external_id): row.payload for row in rows}
    assert payloads[repo.namespace, "duplicate"] == first.model_dump(exclude_defaults=True)
    assert payloads[other.namespace, "duplicate"] == changed.model_dump(exclude_defaults=True)


async def campaign_store(repo: Repository) -> RedStore:
    repo.settings.accounts = ["10", "11"]
    now = datetime.now(UTC)
    store = RedStore(repo.db)
    store.scope = "synthetic-efficiency-" + uuid4().hex
    store.config = RedSettings(
        _env_file=None,
        expected_team_id=99,
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(hours=47),
        archive_namespace=repo.namespace,
        sessions_json='[{"account_id":10,"token":"fixture"},{"account_id":11,"token":"fixture2"}]',
    )
    await store.initialize()
    return store


async def plan_fixture(store: RedStore, identity: int, author: int, account: str = "10") -> bool:
    await store.observe([ObservationInput(external_id=str(identity), text="Synthetic referendum")])
    item = await store.find_observation(str(identity))
    assert item is not None
    return await store.plan_reply(
        item.id, author, account, Draft(target_id=str(identity), text="Fixture reply")
    )


@pytest.mark.integration
@pytest.mark.parametrize("first_operation", ["quarantine", "plan"])
async def test_quarantine_and_planning_serialize_at_campaign_lock(
    repo: Repository, first_operation: str
) -> None:
    store = await campaign_store(repo)
    await store.observe([ObservationInput(external_id="100", text="Synthetic referendum")])
    item = await store.find_observation("100")
    assert item is not None
    prefix = "race-" + uuid4().hex
    backend_ids = {}

    def record_lock_wait(connection, cursor, statement, parameters, context, executemany):
        task = asyncio.current_task()
        if task and task.get_name().startswith(prefix) and "FOR UPDATE" in statement:
            backend_ids[task.get_name()] = connection.connection.driver_connection.info.backend_pid

    async def wait_for_lock(task):
        async with asyncio.timeout(8):
            while True:
                if task.done():
                    await task
                    pytest.fail("Operation finished before reaching the campaign lock")
                backend_id = backend_ids.get(task.get_name())
                if backend_id is not None:
                    async with repo.db.sessions() as observer:
                        waiting = await observer.scalar(
                            text(
                                "SELECT wait_event_type = 'Lock' FROM pg_stat_activity "
                                "WHERE pid = :pid"
                            ),
                            {"pid": backend_id},
                        )
                    if waiting:
                        return
                await asyncio.sleep(0.01)

    operations = {
        "quarantine": lambda: store.quarantine("10", "operator_disabled"),
        "plan": lambda: store.plan_reply(
            item.id, 7, "10", Draft(target_id="100", text="Fixture reply")
        ),
    }
    tasks = []
    event.listen(repo.db.engine.sync_engine, "before_cursor_execute", record_lock_wait)
    try:
        async with repo.db.sessions.begin() as blocker:
            await blocker.scalar(
                select(RedCampaign).where(RedCampaign.scope == store.scope).with_for_update()
            )
            for operation in [
                first_operation,
                "plan" if first_operation == "quarantine" else "quarantine",
            ]:
                task = asyncio.create_task(operations[operation](), name=prefix + operation)
                tasks.append(task)
                await wait_for_lock(task)
        async with asyncio.timeout(8):
            results = await asyncio.gather(*tasks)
        planned = results[0 if first_operation == "plan" else 1]
        assert planned is (first_operation == "plan")
    finally:
        event.remove(repo.db.engine.sync_engine, "before_cursor_execute", record_lock_wait)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    async with repo.db.sessions() as session:
        actions = list(
            await session.scalars(select(Action).where(Action.namespace == store.namespace))
        )
        targets = list(
            await session.scalars(select(RedTarget).where(RedTarget.scope == store.scope))
        )
        decision_count = await session.scalar(
            select(func.count()).select_from(Decision).where(Decision.namespace == store.namespace)
        )
    assert all(action.status == "failed" for action in actions)
    expected = int(first_operation == "plan")
    assert len(actions) == len(targets) == decision_count == expected
    assert not await plan_fixture(store, 101, 8)
    assert await plan_fixture(store, 102, 9, "11")


@pytest.mark.integration
async def test_quarantine_keeps_unknown_and_inflight_history_and_reservations(
    repo: Repository,
) -> None:
    store = await campaign_store(repo)
    for identity, author in [(100, 7), (101, 8), (102, 9)]:
        assert await plan_fixture(store, identity, author)
    unknown = await store.claim(["10"])
    assert unknown is not None
    await store.finish(unknown, ActionStatus.UNKNOWN, "synthetic_ambiguous", {})
    inflight = await store.claim(["10"])
    assert inflight is not None
    before = await store.summary()
    await store.quarantine("10", "operator_disabled")
    await store.quarantine("10", "operator_disabled")
    after = await store.summary()
    assert after["counts"] == {**before["counts"], "outcomes": before["counts"]["outcomes"] + 1}
    assert after["action_attempts_last_hour"] == before["action_attempts_last_hour"] == 2
    assert after["action_statuses"] == {"unknown": 1, "in_flight": 1, "failed": 1}
    assert await store.unknown() == [unknown]
    async with repo.db.sessions() as session:
        campaign = await session.get(RedCampaign, store.scope)
        assert campaign is not None and campaign.accounts == ["10", "11"]
        assert campaign.ends_at == store.config.ends_at
        assert (
            await session.scalar(
                select(func.count()).select_from(RedTarget).where(RedTarget.scope == store.scope)
            )
            == 3
        )
    assert not await plan_fixture(store, 103, 7, "11")
    assert await store.claim(["10"]) is None
