"""Parallel red dispatcher keeps one task per bounded action lane."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from botfluencer.red.worker import RedWorker
from botfluencer.runtime.worker import WorkerState


def worker_fixture():
    active: dict[tuple[str, ...], int] = {}
    maximum: dict[tuple[str, ...], int] = {}
    entered = asyncio.Event()
    release = asyncio.Event()

    async def claim(*, kinds):
        return kinds

    async def execute(kinds):
        active[kinds] = active.get(kinds, 0) + 1
        maximum[kinds] = max(maximum.get(kinds, 0), active[kinds])
        entered.set()
        await release.wait()
        active[kinds] -= 1
        return None

    now = datetime.now(UTC)
    store = SimpleNamespace(
        is_paused=AsyncMock(return_value=False),
        pending_count=AsyncMock(return_value=4),
        claim=AsyncMock(side_effect=claim),
        heartbeat=AsyncMock(),
    )
    config = SimpleNamespace(
        parallel_lanes=True,
        like_concurrency=4,
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(hours=1),
        max_pending_actions=8,
    )
    obj = RedWorker.__new__(RedWorker)
    obj.store, obj.config = store, config
    obj.executor = SimpleNamespace(transport=SimpleNamespace(clock=lambda: now))
    obj.id = "worker"
    obj.state = WorkerState()
    obj._planner = None
    obj._parallel_tasks = {}
    obj._parallel_planners = {}
    obj.engagement_planner = None
    obj.settings = SimpleNamespace(operation_timeout_seconds=5)
    obj._next_engagement_reconciliation = 0.0
    obj._parallel_publication_prefers_boost = False
    obj._parallel_plan_lock = asyncio.Lock()
    obj.execute = AsyncMock(side_effect=execute)
    obj._plan_editorial = AsyncMock(return_value=False)
    return obj, store, entered, release, maximum


async def test_parallel_dispatch_claims_distinct_bounded_lanes_and_keeps_heartbeat() -> None:
    worker, store, entered, release, maximum = worker_fixture()

    await worker._parallel_contextual_tick()
    await asyncio.wait_for(entered.wait(), timeout=1)
    await worker._parallel_contextual_tick()

    assert worker.state.heartbeat_monotonic is not None
    assert store.heartbeat.await_count == 2
    assert {call.kwargs["kinds"] for call in store.claim.await_args_list} == {
        ("like",),
        ("post", "reply", "repost", "team_reply"),
    }
    assert len(worker._parallel_tasks) == 5
    assert store.claim.await_args_list[0].kwargs["kinds"] == (
        "post",
        "reply",
        "repost",
        "team_reply",
    )

    release.set()
    await asyncio.gather(*worker._parallel_tasks.values())
    assert maximum[("like",)] == 4
    assert maximum[("post", "reply", "repost", "team_reply")] == 1


async def test_parallel_lane_failure_is_supervised_as_fatal() -> None:
    worker, store, *_ = worker_fixture()
    store.claim.side_effect = RuntimeError("synthetic fatal dispatch failure")
    await worker._parallel_contextual_tick()
    await asyncio.sleep(0)
    try:
        await worker._parallel_contextual_tick()
    except RuntimeError as error:
        assert "synthetic fatal" in str(error)
    else:
        raise AssertionError("completed dispatcher failure was not propagated")


async def test_background_tasks_stop_before_namespace_ownership_is_released() -> None:
    from botfluencer.runtime.worker import Worker

    events: list[str] = []

    class Connection:
        closed = False
        invalidated = False

        async def scalar(self, _query):
            return 17

        async def commit(self):
            return None

    @asynccontextmanager
    async def leadership():
        events.append("acquired")
        try:
            yield Connection()
        finally:
            events.append("released")

    class Repo:
        settings = SimpleNamespace(
            operation_timeout_seconds=1,
            poll_interval_seconds=0,
            retry_base_seconds=0.1,
            max_action_attempts=2,
            accounts=[],
        )
        namespace = "synthetic"
        db = SimpleNamespace(leadership=leadership)

        async def initialize(self):
            return None

        async def recover(self):
            return None

    class HookWorker(Worker):
        async def tick(self):
            events.append("tick")

        async def before_leadership_release(self):
            events.append("stop_children")

    worker = HookWorker.__new__(HookWorker)
    worker.repo = Repo()
    worker.settings = worker.repo.settings
    worker.state = WorkerState()
    worker.stop = asyncio.Event()
    worker.id = "synthetic-worker"
    await worker.run(ticks=1)
    assert events == ["acquired", "tick", "stop_children", "released"]


async def test_empty_lanes_do_not_launch_idle_claims():
    worker, store, *_ = worker_fixture()
    store.pending_count.return_value = 0
    await worker._parallel_contextual_tick()
    await asyncio.sleep(0)
    store.claim.assert_not_awaited()
    assert not worker._parallel_tasks
    await worker.before_leadership_release()
