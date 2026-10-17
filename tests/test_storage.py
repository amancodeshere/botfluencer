import asyncio

import pytest
from sqlalchemy import func, select

from botfluencer.domain import ActionStatus, Draft, ObservationInput
from botfluencer.storage.database import Database
from botfluencer.storage.models import MockReceipt
from botfluencer.storage.repository import Repository

pytestmark = pytest.mark.integration


async def plan(repo: Repository, external_id: str = "post-1", key: str = "a" * 64) -> None:
    item = ObservationInput(external_id=external_id, text="Synthetic test observation")
    await repo.observe([item])
    observation = await repo.unplanned("mock-01", "baseline-v1", "mock-v1")
    assert observation is not None
    await repo.plan(
        observation.id,
        "mock-01",
        key,
        "baseline-v1",
        "mock-v1",
        Draft(target_id=item.external_id, text="Synthetic reply"),
    )


async def test_observation_and_intent_deduplicate_under_concurrent_calls(repo: Repository) -> None:
    item = ObservationInput(external_id="post", text="Synthetic input")
    await asyncio.gather(repo.observe([item]), repo.observe([item]))
    observation = await repo.unplanned("mock-01", "v1", "p1")
    assert observation is not None
    args = (observation.id, "mock-01", "a" * 64, "v1", "p1", Draft(target_id="post", text="Mock"))
    ids = await asyncio.gather(repo.plan(*args), repo.plan(*args))
    assert ids[0] == ids[1]
    summary = await repo.summary()
    assert summary["counts"] == {"observations": 1, "decisions": 1, "actions": 1, "outcomes": 0}


async def test_pause_and_budgets_survive_new_repository(repo: Repository) -> None:
    await plan(repo)
    await repo.set_paused(True)
    restarted_db = Database(repo.settings)
    restarted = Repository(restarted_db)
    try:
        await restarted.initialize()
        assert await restarted.is_paused()
        assert await restarted.claim() is None
        assert not await restarted.reserve_generation("mock-01")
        await restarted.set_paused(False)
        repo.settings.max_actions_per_hour = 1
        assert await restarted.claim() is not None
        await plan(repo, "post-2", "b" * 64)
        assert await repo.claim() is None
        repo.settings.max_llm_calls_per_hour = 1
        assert await restarted.reserve_generation("mock-01")
        assert not await repo.reserve_generation("mock-01")
    finally:
        await restarted_db.close()


async def test_recovery_retains_remote_receipt_without_duplicate_execution(
    repo: Repository,
) -> None:
    await plan(repo)
    intent = await repo.claim()
    assert intent is not None
    receipt = await repo.mock_execute(intent)
    # Crash after acceptance, before the worker records the outcome.
    await repo.recover()
    unknown = await repo.unknown()
    assert unknown == [intent]
    assert await repo.mock_lookup(intent.key) == receipt
    await repo.finish(intent, ActionStatus.SUCCEEDED, "reconciled", receipt.model_dump())
    await repo.finish(intent, ActionStatus.SUCCEEDED, "reconciled", receipt.model_dump())
    assert await repo.claim() is None
    async with repo.db.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(MockReceipt)
                .where(MockReceipt.namespace == repo.namespace)
            )
            == 1
        )
    summary = await repo.summary()
    assert summary["counts"]["outcomes"] == 1
    assert summary["action_attempts_last_hour"] == 1


async def test_leadership_is_exclusive_and_released_on_close(repo: Repository) -> None:
    second = Database(repo.settings)
    try:
        async with repo.db.leadership():
            with pytest.raises(RuntimeError, match="Another worker"):
                async with second.leadership():
                    pytest.fail("Second worker acquired the same namespace")
        async with second.leadership():
            pass
    finally:
        await second.close()


async def test_namespace_cannot_be_reused_for_another_mode(repo: Repository) -> None:
    other = Database(repo.settings.model_copy(update={"mode": "observe"}))
    try:
        with pytest.raises(RuntimeError, match="Namespace mode differs"):
            await Repository(other).initialize()
    finally:
        await other.close()


async def test_excluded_accounts_cannot_claim_persisted_intents(repo: Repository) -> None:
    from botfluencer.runtime.service import build_worker

    await plan(repo)
    assert await repo.claim([]) is None
    assert await repo.claim(["another-account"]) is None
    repo.settings.strategy = "noop"
    await build_worker(repo.db).run(ticks=1)
    assert (await repo.summary())["action_statuses"] == {"queued": 1}
    assert await repo.claim(["mock-01"]) is not None
