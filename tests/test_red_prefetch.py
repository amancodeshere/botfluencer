"""The single planner may prepare work while the sole publication awaits its result."""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from botfluencer.red.worker import RedWorker
from botfluencer.runtime.worker import WorkerState


def contextual_worker(*, paused: bool = False, pending: int = 1):
    now = datetime.now(UTC)
    intent = object()
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
    worker.store = SimpleNamespace(
        is_paused=AsyncMock(return_value=paused),
        claim=AsyncMock(return_value=intent),
        pending_count=AsyncMock(return_value=pending),
        heartbeat=AsyncMock(),
        observe=AsyncMock(),
    )
    worker.execute = AsyncMock()
    worker.plan_contextual = AsyncMock()
    return worker, intent


async def cancel_task(task):
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_prefetch_prepares_before_blocked_publication_finishes() -> None:
    worker, intent = contextual_worker()
    publisher_started = asyncio.Event()
    publisher_release = asyncio.Event()
    planner_prepared = asyncio.Event()
    publications = []

    async def publish(action):
        publications.append(action)
        publisher_started.set()
        await publisher_release.wait()

    async def prepare():
        await worker.store.observe([])
        planner_prepared.set()

    worker.execute.side_effect = publish
    worker.plan_contextual.side_effect = prepare
    tick = asyncio.create_task(worker.contextual_tick())
    try:
        async with asyncio.timeout(3):
            await publisher_started.wait()
            await planner_prepared.wait()
        assert not tick.done() and not publisher_release.is_set()
        assert publications == [intent]
        worker.execute.assert_awaited_once_with(intent)
        worker.plan_contextual.assert_awaited_once()
        assert worker._planner is not None and worker._planner.done()
        publisher_release.set()
        async with asyncio.timeout(3):
            await tick
        worker.store.heartbeat.assert_awaited_once_with(worker.id)
    finally:
        publisher_release.set()
        await cancel_task(tick)
        await cancel_task(worker._planner)


@pytest.mark.parametrize("capacity", [1, 4])
async def test_full_queue_blocks_prefetch_without_blocking_publication(capacity: int) -> None:
    worker, intent = contextual_worker(pending=capacity)
    worker.config.max_pending_actions = capacity
    publisher_started = asyncio.Event()
    publisher_release = asyncio.Event()

    async def publish(_):
        publisher_started.set()
        await publisher_release.wait()

    worker.execute.side_effect = publish
    tick = asyncio.create_task(worker.contextual_tick())
    try:
        async with asyncio.timeout(3):
            await publisher_started.wait()
        assert not tick.done()
        worker.plan_contextual.assert_not_awaited()
        assert worker._planner is None
        publisher_release.set()
        async with asyncio.timeout(3):
            await tick
        worker.execute.assert_awaited_once_with(intent)
        worker.store.heartbeat.assert_awaited_once_with(worker.id)
    finally:
        publisher_release.set()
        await cancel_task(tick)
        await cancel_task(worker._planner)


async def test_paused_tick_keeps_heartbeat_without_prefetch_or_publication() -> None:
    worker, _ = contextual_worker(paused=True)
    await worker.contextual_tick()
    worker.store.claim.assert_not_awaited()
    worker.store.pending_count.assert_not_awaited()
    worker.plan_contextual.assert_not_awaited()
    worker.execute.assert_not_awaited()
    worker.store.heartbeat.assert_awaited_once_with(worker.id)
    assert worker._planner is None


async def test_active_planner_is_reused_while_publication_continues() -> None:
    worker, intent = contextual_worker()
    planner_started = asyncio.Event()
    planner_release = asyncio.Event()

    async def active_planner():
        planner_started.set()
        await planner_release.wait()

    prior = asyncio.create_task(active_planner())
    worker._planner = prior
    try:
        async with asyncio.timeout(3):
            await planner_started.wait()
            await worker.contextual_tick()
        assert worker._planner is prior and not prior.done()
        worker.plan_contextual.assert_not_awaited()
        worker.store.pending_count.assert_not_awaited()
        worker.execute.assert_awaited_once_with(intent)
        worker.store.heartbeat.assert_awaited_once_with(worker.id)
    finally:
        planner_release.set()
        await cancel_task(prior)


async def test_queue_read_failure_after_claim_propagates_before_any_publication() -> None:
    worker, _ = contextual_worker()
    worker.store.pending_count.side_effect = RuntimeError("Synthetic queue read failure")
    with pytest.raises(RuntimeError, match="Synthetic queue read failure"):
        await worker.contextual_tick()
    worker.store.claim.assert_awaited_once()
    worker.execute.assert_not_awaited()
    worker.plan_contextual.assert_not_awaited()
    assert worker._planner is None
