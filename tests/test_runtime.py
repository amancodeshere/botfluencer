import asyncio
import time
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from botfluencer.domain import ActionIntent, Receipt, Reconciliation
from botfluencer.llm.mock import MockGenerator
from botfluencer.platform.base import PermanentRejection, SafeRetry
from botfluencer.platform.mock import MockPlatform
from botfluencer.reporting.health import create_app
from botfluencer.runtime.service import build_worker, supervise
from botfluencer.runtime.worker import Worker
from botfluencer.storage.models import Action, MockReceipt
from botfluencer.storage.repository import Repository

pytestmark = pytest.mark.integration


async def test_end_to_end_replay_and_restart_do_not_duplicate(repo: Repository) -> None:
    await build_worker(repo.db).run(ticks=4)
    summary = await repo.summary()
    assert summary["counts"] == {"observations": 3, "decisions": 3, "actions": 3, "outcomes": 3}
    await build_worker(repo.db).run(ticks=2)
    assert (await repo.summary())["counts"] == summary["counts"]


async def test_observe_mode_never_generates_or_executes(repo: Repository) -> None:
    # This test owns a fresh namespace with no actions; initialize it in observe mode.
    async with repo.db.sessions.begin() as session:
        from botfluencer.storage.models import Control

        await session.execute(
            update(Control).where(Control.namespace == repo.namespace).values(mode="observe")
        )
    repo.settings.mode = "observe"
    await build_worker(repo.db).run(ticks=2)
    assert (await repo.summary())["counts"] == {
        "observations": 3,
        "decisions": 0,
        "actions": 0,
        "outcomes": 0,
    }


async def test_paused_worker_keeps_heartbeat_but_takes_no_actions(repo: Repository) -> None:
    await repo.set_paused(True)
    worker = build_worker(repo.db)
    await worker.run(ticks=2)
    summary = await repo.summary()
    assert summary["heartbeat_at"] is not None
    assert summary["paused"] is True
    assert summary["counts"]["observations"] == 0


class AcceptedThenTimeout(MockPlatform):
    async def execute(self, intent: ActionIntent) -> Receipt:
        await super().execute(intent)
        raise TimeoutError("synthetic timeout after remote acceptance")


async def test_ambiguous_acceptance_reconciles_after_restart(repo: Repository) -> None:
    worker = Worker(repo, AcceptedThenTimeout(repo, repo.settings.fixture_path), MockGenerator())
    await worker.run(ticks=1)
    assert (await repo.summary())["action_statuses"] == {"unknown": 1}
    await build_worker(repo.db).run(ticks=4)
    summary = await repo.summary()
    assert summary["action_statuses"] == {"succeeded": 3}
    assert summary["action_attempts_last_hour"] == 3
    async with repo.db.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(MockReceipt)
                .where(MockReceipt.namespace == repo.namespace)
            )
            == 3
        )


class UnresolvedPlatform(MockPlatform):
    calls: list[str]

    def __init__(self, repo: Repository) -> None:
        super().__init__(repo, repo.settings.fixture_path)
        self.calls = []

    async def execute(self, intent: ActionIntent) -> Receipt:
        self.calls.append(intent.key)
        raise TimeoutError("Uncertain acceptance")

    async def reconcile(self, intent: ActionIntent) -> Reconciliation:
        return Reconciliation(state="unresolved")


async def test_unresolved_actions_are_never_blindly_retried(repo: Repository) -> None:
    platform = UnresolvedPlatform(repo)
    await Worker(repo, platform, MockGenerator()).run(ticks=5)
    assert len(platform.calls) == len(set(platform.calls)) == 3
    assert (await repo.summary())["action_statuses"] == {"unknown": 3}


class RateLimitedPlatform(MockPlatform):
    async def execute(self, intent: ActionIntent) -> Receipt:
        raise SafeRetry(retry_after_seconds=10)


async def test_safe_retry_persists_backoff_and_attempt_limit(repo: Repository) -> None:
    platform = RateLimitedPlatform(repo, repo.settings.fixture_path)
    worker = Worker(repo, platform, MockGenerator())
    await worker.run(ticks=1)
    async with repo.db.sessions() as session:
        action = await session.scalar(select(Action).where(Action.namespace == repo.namespace))
        assert action is not None
        assert action.status == "queued" and action.attempts == 1
        now = await session.scalar(select(func.now()))
        assert (action.next_attempt_at - now).total_seconds() > 5
    repo.settings.max_action_attempts = 1
    async with repo.db.sessions.begin() as session:
        await session.execute(
            update(Action)
            .where(Action.namespace == repo.namespace)
            .values(next_attempt_at=func.now())
        )
    assert await repo.claim() is None
    assert (await repo.summary())["action_statuses"] == {"failed": 1}


async def test_cooldown_survives_worker_restart(repo: Repository) -> None:
    repo.settings.account_cooldown_seconds = 60
    await build_worker(repo.db).run(ticks=3)
    await build_worker(repo.db).run(ticks=2)
    summary = await repo.summary()
    assert summary["action_statuses"] == {"succeeded": 1, "queued": 2}
    assert summary["action_attempts_last_hour"] == 1


async def test_zero_budgets_prevent_generation_and_execution(repo: Repository) -> None:
    repo.settings.max_llm_calls_per_hour = 0
    await build_worker(repo.db).run(ticks=2)
    assert (await repo.summary())["counts"]["decisions"] == 0
    repo.settings.max_llm_calls_per_hour = 60
    repo.settings.max_actions_per_hour = 0
    await build_worker(repo.db).run(ticks=3)
    assert (await repo.summary())["action_attempts_last_hour"] == 0


async def test_health_rejects_stale_worker_and_accepts_standby(repo: Repository) -> None:
    worker = build_worker(repo.db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(worker)), base_url="http://test"
    ) as client:
        assert (await client.get("/health/ready")).status_code == 503
        worker.state.initialized = True
        worker.state.role = "standby"
        assert (await client.get("/health/ready")).status_code == 200
        assert (await client.get("/health/worker")).status_code == 503
        worker.state.role = "active"
        worker.state.heartbeat_monotonic = time.monotonic()
        assert (await client.get("/health/worker")).status_code == 200
        worker.state.heartbeat_monotonic -= 100
        assert (await client.get("/health/worker")).status_code == 503
        assert (await client.get("/health/ready")).status_code == 503
        worker.state.role = "failed"
        assert (await client.get("/health/ready")).status_code == 503


async def test_overlap_waits_for_ownership_then_continues(repo: Repository) -> None:
    worker = build_worker(repo.db)
    async with repo.db.leadership():
        task = asyncio.create_task(worker.run(ticks=1))
        for _ in range(100):
            if worker.state.initialized:
                break
            await asyncio.sleep(0.01)
        assert worker.state.role == "standby"
        assert (await repo.summary())["counts"]["actions"] == 0
    await asyncio.wait_for(task, timeout=3)
    assert (await repo.summary())["action_statuses"] == {"succeeded": 1}


class BrokenGenerator(MockGenerator):
    async def generate(self, observation: Any) -> Any:
        raise RuntimeError("synthetic fatal worker error")


class FakeServer:
    should_exit = False
    started = True

    async def serve(self) -> None:
        while not self.should_exit:  # noqa: ASYNC110 - emulate uvicorn's exit flag in this test
            await asyncio.sleep(0.01)


async def test_supervisor_propagates_worker_failure_and_stops_http(repo: Repository) -> None:
    worker = Worker(repo, MockPlatform(repo, repo.settings.fixture_path), BrokenGenerator())
    server = FakeServer()
    with pytest.raises(RuntimeError, match="synthetic fatal"):
        await asyncio.wait_for(supervise(worker, server), timeout=3)  # type: ignore[arg-type]
    assert worker.state.role == "failed"
    assert server.should_exit


class RejectedPlatform(MockPlatform):
    async def execute(self, intent: ActionIntent) -> Receipt:
        raise PermanentRejection("Rejected synthetic request")


async def test_permanent_rejection_is_not_retried(repo: Repository) -> None:
    platform = RejectedPlatform(repo, repo.settings.fixture_path)
    await Worker(repo, platform, MockGenerator()).run(ticks=5)
    summary = await repo.summary()
    assert summary["action_statuses"] == {"failed": 3}
    assert summary["action_attempts_last_hour"] == 3


class StalledGenerator(MockGenerator):
    async def generate(self, observation: Any) -> Any:
        await asyncio.Event().wait()


async def test_stalled_generation_fails_within_operation_deadline(repo: Repository) -> None:
    repo.settings.operation_timeout_seconds = 0.2
    worker = Worker(repo, MockPlatform(repo, repo.settings.fixture_path), StalledGenerator())
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(worker.run(ticks=1), timeout=3)
    assert worker.state.role == "failed"
    assert (await repo.summary())["action_attempts_last_hour"] == 0


async def test_lost_owner_connection_stops_before_next_tick(repo: Repository) -> None:
    import hashlib

    from sqlalchemy import text

    worker = build_worker(repo.db)
    repo.settings.poll_interval_seconds = 0.2
    task = asyncio.create_task(worker.run())
    try:
        for _ in range(100):
            if worker.state.heartbeat_monotonic is not None:
                break
            await asyncio.sleep(0.01)
        async with repo.db.engine.connect() as connection:
            # Terminate only this test namespace's advisory-lock owner.
            key = int.from_bytes(hashlib.sha256(repo.namespace.encode()).digest()[:8])
            pid = await connection.scalar(
                text(
                    "SELECT pid FROM pg_locks WHERE locktype='advisory' AND granted "
                    "AND classid=:high AND objid=:low AND objsubid=1 "
                    "AND database=(SELECT oid FROM pg_database WHERE datname=current_database())"
                ),
                {"high": key >> 32, "low": key & 0xFFFFFFFF},
            )
            assert pid is not None
            await connection.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
            await connection.commit()
        with pytest.raises(DBAPIError):
            await asyncio.wait_for(task, timeout=3)
        assert worker.state.role == "failed"
        assert (await repo.summary())["action_attempts_last_hour"] == 1
    finally:
        worker.stop.set()
        if not task.done():
            await task
