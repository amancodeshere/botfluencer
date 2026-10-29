from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select, update

from botfluencer.domain import Draft, ObservationInput
from botfluencer.red.config import RedSettings
from botfluencer.red.policy import REPLIES, draft_for, eligible
from botfluencer.red.store import DISPATCHER, PROMPT_VERSION, STRATEGY_VERSION, RedStore
from botfluencer.storage.models import RedCampaign, RedTarget
from botfluencer.storage.repository import Repository


def config(namespace: str, **overrides) -> RedSettings:
    now = datetime.now(UTC)
    values = dict(
        _env_file=None,
        expected_team_id=99,
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(hours=47),
        max_attempts=10,
        archive_namespace=namespace,
        sessions_json=SecretStr('[{"account_id":10,"token":"synthetic-token"}]'),
    )
    values.update(overrides)
    return RedSettings(**values)


def observation(identity: int = 123, **metadata) -> ObservationInput:
    values = dict(
        post_id=identity,
        author_id=7,
        kind="post",
        root_post_id=identity,
        created_at=datetime.now(UTC).isoformat(),
    )
    values.update(metadata)
    return ObservationInput(
        external_id=str(identity), text="Discuss the referendum", source="social", metadata=values
    )


@pytest.mark.parametrize("field", ["starts_at", "ends_at"])
def test_campaign_requires_explicit_timezone(field: str) -> None:
    with pytest.raises(ValidationError):
        config("synthetic", **{field: datetime.now()})


@pytest.mark.parametrize(
    "metadata",
    [
        {"author_id": 8},
        {"kind": "reply"},
        {"reply_to_post_id": 99},
        {"root_post_id": 99},
        {"created_at": (datetime.now(UTC) - timedelta(hours=5)).isoformat()},
        {"created_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()},
        {"created_at": "malformed"},
        {"created_at": datetime.now().isoformat()},
    ],
)
def test_policy_rejects_unconfirmed_threaded_stale_or_invalid_posts(metadata: dict) -> None:
    assert not eligible(observation(**metadata), frozenset({7}), datetime.now(UTC))


def test_policy_requires_public_topic_and_preserves_target() -> None:
    item = observation()
    assert eligible(item, frozenset({7}), datetime.now(UTC))
    item.text = "Unrelated synthetic weather post"
    assert not eligible(item, frozenset({7}), datetime.now(UTC))
    item.source = "fixture"
    assert not eligible(item, frozenset({7}), datetime.now(UTC))
    assert draft_for(item).target_id == item.external_id
    assert all(0 < len(reply) <= 500 and "@" not in reply for reply in REPLIES)


async def initialized_store(repo: Repository, **overrides) -> RedStore:
    repo.settings.accounts = ["10"]
    store = RedStore(repo.db)
    store.config = config(repo.namespace, **overrides)
    store.scope = "synthetic-" + uuid4().hex
    await store.initialize()
    return store


async def plan(store: RedStore, identity: int, author: int) -> bool:
    await store.observe([observation(identity, author_id=author)])
    item = await store.unplanned(DISPATCHER, STRATEGY_VERSION, PROMPT_VERSION)
    assert item is not None
    return await store.plan_reply(
        item.id, author, "10", Draft(target_id=str(identity), text="Fixture")
    )


@pytest.mark.integration
async def test_campaign_mandate_cannot_be_extended_or_moved(repo: Repository) -> None:
    store = await initialized_store(repo)
    store.config = store.config.model_copy(
        update={"ends_at": store.config.ends_at + timedelta(minutes=1)}
    )
    with pytest.raises(ValueError, match="Persisted campaign differs"):
        await store.initialize()
    async with repo.db.sessions() as session:
        saved = await session.get(RedCampaign, store.scope)
        assert saved is not None
        store.config = store.config.model_copy(update={"ends_at": saved.ends_at})
    other_db = repo.db.settings.model_copy(update={"namespace": repo.namespace + "-other"})
    from botfluencer.storage.database import Database

    database = Database(other_db)
    other = RedStore(database)
    other.scope, other.config = store.scope, store.config
    try:
        with pytest.raises(ValueError, match="Persisted campaign differs"):
            await other.initialize()
    finally:
        await database.close()


@pytest.mark.integration
async def test_target_dedupe_and_daily_author_contact_survive_restart(repo: Repository) -> None:
    store = await initialized_store(repo)
    assert await plan(store, 123, 7)
    # Another account cannot reserve the same post or author in the same team scope.
    await store.observe([observation(124)])
    item = await store.unplanned(DISPATCHER, STRATEGY_VERSION, PROMPT_VERSION)
    assert item is not None
    assert not await store.plan_reply(item.id, 7, "10", Draft(target_id="123", text="Fixture"))
    assert not await store.plan_reply(item.id, 7, "10", Draft(target_id="124", text="Fixture"))
    restarted = RedStore(repo.db)
    restarted.config, restarted.scope = store.config, store.scope
    await restarted.initialize()
    assert not await restarted.plan_reply(item.id, 7, "10", Draft(target_id="124", text="Fixture"))
    async with repo.db.sessions.begin() as session:
        await session.execute(
            update(RedTarget)
            .where(RedTarget.scope == store.scope)
            .values(created_at=func.now() - timedelta(hours=25))
        )
    assert await restarted.plan_reply(item.id, 7, "10", Draft(target_id="124", text="Fixture"))


@pytest.mark.integration
async def test_skipped_observations_persist_and_advance(repo: Repository) -> None:
    store = await initialized_store(repo)
    await store.observe([observation(123), observation(124)])
    item = await store.unplanned(DISPATCHER, STRATEGY_VERSION, PROMPT_VERSION)
    assert item is not None
    await store.skip(item.id, item.external_id, "Synthetic irrelevant post")
    await store.skip(item.id, item.external_id, "Synthetic duplicate skip")
    next_item = await store.unplanned(DISPATCHER, STRATEGY_VERSION, PROMPT_VERSION)
    assert next_item is not None and next_item.id != item.id
    assert (await store.summary())["counts"]["actions"] == 0


@pytest.mark.integration
async def test_claim_respects_pause_deadline_and_total_attempt_cap(repo: Repository) -> None:
    store = await initialized_store(repo, max_attempts=2)
    assert await plan(store, 123, 7)
    assert await plan(store, 124, 8)
    await store.set_paused(True)
    assert await store.claim() is None
    await store.set_paused(False)
    async with repo.db.sessions.begin() as session:
        await session.execute(
            update(RedCampaign)
            .where(RedCampaign.scope == store.scope)
            .values(ends_at=func.now() - timedelta(seconds=1))
        )
    assert await store.claim() is None
    async with repo.db.sessions.begin() as session:
        await session.execute(
            update(RedCampaign)
            .where(RedCampaign.scope == store.scope)
            .values(ends_at=store.config.ends_at, max_attempts=1)
        )
    assert await store.claim() is not None
    assert await store.claim() is None
    async with repo.db.sessions() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(RedTarget).where(RedTarget.scope == store.scope)
            )
            == 2
        )
    assert (await store.summary())["action_attempts_last_hour"] == 1
