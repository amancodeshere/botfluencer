"""Persisted publication balance and bounded NPC reply discovery."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from botfluencer.competition.errors import RequestDeferred
from botfluencer.domain import ActionStatus, Draft, ObservationInput
from botfluencer.platform.base import SafeRetry
from botfluencer.red.config import RedSettings
from botfluencer.red.store import RedStore
from botfluencer.red.worker import RedWorker
from botfluencer.runtime.worker import WorkerState
from botfluencer.storage.models import Action
from botfluencer.storage.repository import Repository


def npc_observation(identity: int = 123, *, valid: bool = True, threaded: bool = False):
    item = ObservationInput(
        external_id=str(identity),
        text="Synthetic referendum discussion" if valid else "Weather",
        source="social",
        metadata={
            "post_id": identity,
            "author_id": 7,
            "kind": "reply" if threaded else "post",
            "root_post_id": 100 if threaded else identity,
            "created_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        },
    )
    return SimpleNamespace(id=uuid4(), external_id=item.external_id, payload=item.model_dump())


def planner(monkeypatch, tmp_path, *, due: bool = True, latest: str | None = "post"):
    import botfluencer.red.worker as module

    now = [datetime.now(UTC)]
    preference = {"due": due, "latest": latest}
    store = SimpleNamespace(
        is_paused=AsyncMock(return_value=False),
        next_account=AsyncMock(return_value="10"),
        planning_preference=AsyncMock(
            side_effect=lambda: (preference["due"], preference["latest"])
        ),
        original_streak=AsyncMock(return_value=0),
        candidates=AsyncMock(return_value=[]),
        continuation_candidates=AsyncMock(return_value=[]),
        observe=AsyncMock(),
        unplanned=AsyncMock(return_value=None),
        contact_available=AsyncMock(return_value=True),
        reserve_generation=AsyncMock(return_value=True),
        plan_reply=AsyncMock(return_value=True),
        skip=AsyncMock(),
        find_observation=AsyncMock(),
    )
    store.find_observation.side_effect = lambda identity: SimpleNamespace(
        id=uuid4(), external_id=identity
    )
    generator = SimpleNamespace(
        prompt_version="synthetic-scheduler",
        generate=AsyncMock(
            side_effect=lambda item, **kwargs: Draft(
                target_id=item.external_id, text="Synthetic civic argument", kind=kwargs["kind"]
            )
        ),
    )
    world = SimpleNamespace(
        require_fresh=Mock(),
        digest="synthetic-context",
        sources=[SimpleNamespace(title="Referendum")],
    )
    monkeypatch.setattr(module, "load_world", lambda _: world)
    monkeypatch.setattr(module, "ContextualGenerator", lambda *_args, **_kwargs: generator)
    config = SimpleNamespace(
        world_context_file=tmp_path / "world.json",
        max_world_age_hours=6,
        gif_enabled=False,
        allow_threads=True,
        selector_mode="reviewed",
        ends_at=now[0] + timedelta(hours=1),
        post_interval_seconds=60,
        originals_per_reply=1,
    )
    executor = SimpleNamespace(
        transport=SimpleNamespace(clock=lambda: now[0]),
        npc_author_ids=frozenset({7}),
        _read_preflight=AsyncMock(return_value={}),
        _reply_coordinates=AsyncMock(),
    )

    def make_worker():
        worker = RedWorker.__new__(RedWorker)
        worker.store, worker.config, worker.executor = store, config, executor
        worker.settings = SimpleNamespace(operation_timeout_seconds=30, accounts=["10"])
        return worker

    return make_worker, store, generator, executor, now, preference


@pytest.mark.parametrize(("streak", "expected_kind"), [(1, "post"), (2, "reply")])
async def test_configured_originals_per_reply_uses_durable_post_streak(
    monkeypatch, tmp_path, streak: int, expected_kind: str
) -> None:
    make_worker, store, generator, executor, _, _ = planner(monkeypatch, tmp_path, due=True)
    make_worker().config.originals_per_reply = 2
    store.original_streak.return_value = streak
    store.unplanned.return_value = npc_observation()
    if streak == 1:
        store.continuation_candidates.return_value = [
            ObservationInput(
                external_id="501",
                text="Synthetic direct response",
                source="social",
                metadata={
                    "post_id": 501,
                    "author_id": 7,
                    "kind": "reply",
                    "root_post_id": 100,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
        ]
    await make_worker().plan_contextual()
    assert generator.generate.await_args.kwargs["kind"] == expected_kind
    if expected_kind == "post":
        store.original_streak.assert_awaited_once_with()
        store.unplanned.assert_not_awaited()
        assert store.continuation_candidates.await_count == 0
        executor._read_preflight.assert_not_awaited()
    else:
        store.unplanned.assert_awaited_once()


async def test_slow_original_then_restarted_worker_prefers_reply(monkeypatch, tmp_path) -> None:
    make_worker, store, generator, _, now, preference = planner(monkeypatch, tmp_path, latest=None)
    store.unplanned.return_value = npc_observation()

    async def slow_generate(item, **kwargs):
        now[0] += timedelta(seconds=82)
        return Draft(
            target_id=item.external_id, text="Synthetic civic argument", kind=kwargs["kind"]
        )

    async def persist(*args, **kwargs):
        preference["latest"] = args[3].kind
        return True

    generator.generate.side_effect = slow_generate
    store.plan_reply.side_effect = persist
    await make_worker().plan_contextual()
    await make_worker().plan_contextual()
    assert [call.kwargs["kind"] for call in generator.generate.await_args_list] == ["post", "reply"]
    assert store.plan_reply.await_count == 2
    store.reserve_generation.assert_not_awaited()
    store.planning_preference.assert_awaited()


@pytest.mark.parametrize("due", [False, True])
async def test_empty_npc_scan_falls_back_to_original_only_when_due(
    monkeypatch, tmp_path, due
) -> None:
    make_worker, store, generator, *_ = planner(monkeypatch, tmp_path, due=due)
    await make_worker().plan_contextual()
    store.unplanned.assert_awaited_once()
    if due:
        assert generator.generate.await_args.kwargs["kind"] == "post"
        store.plan_reply.assert_awaited_once()
    else:
        generator.generate.assert_not_awaited()
        store.plan_reply.assert_not_awaited()


@pytest.mark.parametrize("first_problem", ["ineligible", "contacted"])
async def test_reply_scan_advances_from_bad_first_to_valid_second(
    monkeypatch, tmp_path, first_problem
) -> None:
    make_worker, store, generator, *_ = planner(monkeypatch, tmp_path)
    bad = npc_observation(123, valid=first_problem != "ineligible")
    good = npc_observation(124)
    store.unplanned.side_effect = [bad, good]
    if first_problem == "contacted":
        store.contact_available.side_effect = [False, True]
    await make_worker().plan_contextual()
    assert store.unplanned.await_count == 2
    store.skip.assert_awaited_once()
    assert store.skip.await_args.args[0] == bad.id
    assert generator.generate.await_args.args[0].external_id == "124"
    assert generator.generate.await_args.kwargs["kind"] == "reply"
    store.plan_reply.assert_awaited_once()


async def test_reply_scan_stops_after_eight_ineligible_rows(monkeypatch, tmp_path) -> None:
    make_worker, store, generator, *_ = planner(monkeypatch, tmp_path, due=False)
    store.unplanned.side_effect = [npc_observation(i, valid=False) for i in range(1, 10)]
    await make_worker().plan_contextual()
    assert store.unplanned.await_count == store.skip.await_count == 8
    generator.generate.assert_not_awaited()
    store.plan_reply.assert_not_awaited()


@pytest.mark.parametrize("error", [RequestDeferred(0.01), SafeRetry(0), TimeoutError()])
async def test_preflight_deferral_never_falls_back_to_original(
    monkeypatch, tmp_path, error
) -> None:
    import botfluencer.red.worker as module

    make_worker, store, generator, executor, *_ = planner(monkeypatch, tmp_path)
    store.unplanned.return_value = npc_observation(threaded=True)
    executor._read_preflight.side_effect = error
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    await make_worker().plan_contextual()
    generator.generate.assert_not_awaited()
    store.plan_reply.assert_not_awaited()
    store.find_observation.assert_not_awaited()
    store.skip.assert_not_awaited()


@pytest.mark.parametrize("due", [False, True])
@pytest.mark.parametrize("originals_per_reply", [1, 2])
async def test_slow_reply_discovery_times_out_without_skipping_candidate(
    monkeypatch, tmp_path, due: bool, originals_per_reply: int
) -> None:
    make_worker, store, generator, executor, *_ = planner(
        monkeypatch, tmp_path, due=due, latest="post"
    )
    worker = make_worker()
    worker.settings.operation_timeout_seconds = 0.3
    worker.config.originals_per_reply = originals_per_reply
    if due and originals_per_reply > 1:
        store.original_streak.return_value = 2
    store.continuation_candidates.return_value = [
        ObservationInput(
            external_id="501",
            text="Synthetic direct response",
            source="social",
            metadata={
                "post_id": 501,
                "author_id": 7,
                "kind": "reply",
                "root_post_id": 100,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
    ]
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_read(*_args, **_kwargs):
        started.set()
        await release.wait()

    executor._read_preflight.side_effect = blocked_read
    task = asyncio.create_task(worker.plan_contextual())
    await asyncio.wait_for(started.wait(), timeout=1)
    await task

    assert not task.cancelled()
    store.skip.assert_not_awaited()
    if due:
        assert generator.generate.await_args.kwargs["kind"] == "post"
        assert store.observe.await_count == (2 if originals_per_reply > 1 else 1)
        if originals_per_reply > 1:
            assert store.observe.await_args_list[0].args[0] == []
        store.plan_reply.assert_awaited_once()
        assert store.plan_reply.await_args.args[3].kind == "post"
    else:
        generator.generate.assert_not_awaited()
        if originals_per_reply == 1:
            store.observe.assert_not_awaited()
        else:
            store.observe.assert_awaited_once_with([])
        store.plan_reply.assert_not_awaited()


async def test_continuation_timeout_leaves_time_for_ordinary_reply(monkeypatch, tmp_path) -> None:
    make_worker, store, generator, executor, *_ = planner(monkeypatch, tmp_path, due=True)
    worker = make_worker()
    worker.settings.operation_timeout_seconds = 0.9
    worker.config.originals_per_reply = 2
    store.original_streak.return_value = 2
    store.unplanned.return_value = npc_observation(777)
    store.continuation_candidates.return_value = [
        ObservationInput(
            external_id="501",
            text="Synthetic direct response",
            source="social",
            metadata={
                "post_id": 501,
                "author_id": 7,
                "kind": "reply",
                "root_post_id": 100,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
    ]
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_read(*_args, **_kwargs):
        started.set()
        await release.wait()

    executor._read_preflight.side_effect = blocked_read
    task = asyncio.create_task(worker.plan_contextual())
    await asyncio.wait_for(started.wait(), timeout=1)
    await task

    assert generator.generate.await_args.kwargs["kind"] == "reply"
    assert generator.generate.await_args.args[0].external_id == "777"
    store.skip.assert_not_awaited()
    store.plan_reply.assert_awaited_once()
    assert store.plan_reply.await_args.args[3].kind == "reply"


async def test_completed_planner_during_claim_is_replaced_before_publication() -> None:
    now = datetime.now(UTC)
    worker = RedWorker.__new__(RedWorker)
    worker.id = uuid4()
    worker.state = WorkerState()
    worker._planner = None
    worker.config = SimpleNamespace(
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(hours=1),
        max_pending_actions=4,
    )
    worker.executor = SimpleNamespace(transport=SimpleNamespace(clock=lambda: now))
    intent = object()
    previous_started = asyncio.Event()
    planner_release = asyncio.Event()
    next_planner_started = asyncio.Event()
    publication_started = asyncio.Event()
    publication_release = asyncio.Event()

    async def previous_planner():
        previous_started.set()
        await planner_release.wait()

    async def claim():
        planner_release.set()
        assert worker._planner is not None
        await worker._planner
        return intent

    async def next_planner():
        next_planner_started.set()

    async def execute(_intent):
        publication_started.set()
        await publication_release.wait()

    worker.store = SimpleNamespace(
        is_paused=AsyncMock(return_value=False),
        claim=AsyncMock(side_effect=claim),
        pending_count=AsyncMock(return_value=1),
        heartbeat=AsyncMock(),
    )
    worker.execute = AsyncMock(side_effect=execute)
    worker.plan_contextual = AsyncMock(side_effect=next_planner)
    previous = asyncio.create_task(previous_planner())
    worker._planner = previous
    await previous_started.wait()
    tick = asyncio.create_task(worker.contextual_tick())
    try:
        async with asyncio.timeout(1):
            await next_planner_started.wait()
            await publication_started.wait()
        assert previous.done()
        assert worker._planner is not previous
        worker.plan_contextual.assert_awaited_once()
        worker.execute.assert_awaited_once_with(intent)
        publication_release.set()
        await tick
    finally:
        publication_release.set()
        planner_release.set()
        if worker._planner is not None and not worker._planner.done():
            worker._planner.cancel()
        await tick


@pytest.mark.parametrize("stop", ["paused", "expired"])
async def test_pause_or_expiry_during_generation_prevents_intent_save(
    monkeypatch, tmp_path, stop
) -> None:
    make_worker, store, generator, _, now, _ = planner(monkeypatch, tmp_path)
    worker = make_worker()
    store.unplanned.return_value = npc_observation()

    async def generate(item, **kwargs):
        if stop == "paused":
            store.is_paused.return_value = True
        else:
            now[0] = worker.config.ends_at
        return Draft(target_id=item.external_id, text="Fixture", kind=kwargs["kind"])

    generator.generate.side_effect = generate
    await worker.plan_contextual()
    generator.generate.assert_awaited_once()
    store.plan_reply.assert_not_awaited()


@pytest.mark.integration
async def test_planning_preference_uses_namespace_and_persisted_unknown_kind(
    repo: Repository,
) -> None:
    repo.settings.accounts = ["10"]
    now = datetime.now(UTC)
    store = RedStore(repo.db)
    store.scope = "synthetic-scheduler-" + uuid4().hex
    store.config = RedSettings(
        _env_file=None,
        expected_team_id=99,
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(hours=47),
        archive_namespace=repo.namespace,
        post_interval_seconds=60,
        sessions_json='[{"account_id":10,"token":"fixture"}]',
    )
    await store.initialize()
    assert await store.planning_preference() == (True, None)
    target = "post:10:1234"
    await store.observe([ObservationInput(external_id=target, text="Synthetic referendum")])
    observation = await store.find_observation(target)
    assert observation is not None
    assert await store.plan_reply(
        observation.id, 0, "10", Draft(target_id=target, text="Fixture", kind="post")
    )
    action = await store.claim(["10"])
    assert action is not None
    await store.finish(action, ActionStatus.UNKNOWN, "synthetic_ambiguous", {})
    assert await store.planning_preference() == (False, "post")
    async with repo.db.sessions.begin() as session:
        await session.execute(
            update(Action)
            .where(Action.id == action.id)
            .values(created_at=now - timedelta(seconds=82))
        )
    restarted = RedStore(repo.db)
    restarted.scope, restarted.config = store.scope, store.config
    assert await restarted.planning_preference() == (True, "post")
    other = Repository(repo.db)
    other.namespace = repo.namespace + "-unrelated"
    await other.initialize()
    await other.observe([ObservationInput(external_id="500", text="Fixture")])
    async with repo.db.sessions() as session:
        other_observation = await session.scalar(
            select(type(observation)).where(type(observation).namespace == other.namespace)
        )
    other_action = await other.plan(
        other_observation.id,
        "10",
        uuid4().hex,
        "fixture",
        "fixture",
        Draft(target_id="500", text="Fixture", kind="post"),
    )
    async with repo.db.sessions.begin() as session:
        await session.execute(
            update(Action)
            .where(Action.id == other_action)
            .values(created_at=now + timedelta(minutes=1))
        )
    assert await restarted.planning_preference() == (True, "post")
    await store.observe([ObservationInput(external_id="124", text="Synthetic referendum")])
    reply = await store.find_observation("124")
    assert reply is not None
    assert await store.plan_reply(reply.id, 7, "10", Draft(target_id="124", text="Fixture reply"))
    assert await restarted.planning_preference() == (True, "reply")


@pytest.mark.parametrize("seconds,accepted", [(20, True), (19, False)])
def test_original_interval_allows_twenty_seconds_but_rejects_lower(seconds, accepted) -> None:
    from pydantic import ValidationError

    now = datetime.now(UTC)
    values = dict(
        _env_file=None,
        expected_team_id=99,
        starts_at=now,
        ends_at=now + timedelta(hours=1),
        sessions_json='[{"account_id":10,"token":"fixture"}]',
        post_interval_seconds=seconds,
    )
    if accepted:
        assert RedSettings(**values).post_interval_seconds == 20
    else:
        with pytest.raises(ValidationError):
            RedSettings(**values)
