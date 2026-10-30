"""Older dispatcher observations cannot hide recent work or rewrite history."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from botfluencer.domain import ActionStatus, Draft, ObservationInput
from botfluencer.red.config import RedSettings
from botfluencer.red.store import DISPATCHER, PROMPT_VERSION, STRATEGY_VERSION, RedStore
from botfluencer.storage.models import Observation
from botfluencer.storage.repository import Repository


@pytest.mark.integration
async def test_dispatcher_prioritizes_recent_backlog_and_preserves_old_rows_and_unknowns(
    repo: Repository,
) -> None:
    repo.settings.accounts = ["10"]
    now = datetime.now(UTC)
    store = RedStore(repo.db)
    store.scope = "synthetic-backlog-" + uuid4().hex
    store.config = RedSettings(
        _env_file=None,
        expected_team_id=99,
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(hours=47),
        archive_namespace=repo.namespace,
        sessions_json='[{"account_id":10,"token":"fixture"}]',
    )
    await store.initialize()
    older_ids = [str(identity) for identity in range(100, 116)]
    original_id = "post:10:1234"
    orphan_id = "post:10:1111"
    await store.observe(
        [
            *[
                ObservationInput(external_id=identity, text="Old referendum", source="social")
                for identity in older_ids
            ],
            ObservationInput(external_id=original_id, text="Original referendum", source="news"),
            ObservationInput(external_id=orphan_id, text="Orphan news draft", source="news"),
            ObservationInput(external_id="200", text="Fresh first referendum", source="social"),
            ObservationInput(external_id="201", text="Fresh second referendum", source="social"),
        ]
    )
    original = await store.find_observation(original_id)
    assert original is not None
    assert await store.plan_reply(
        original.id, 0, "10", Draft(target_id=original_id, text="Fixture", kind="post")
    )
    action = await store.claim(["10"])
    assert action is not None
    await store.finish(action, ActionStatus.UNKNOWN, "synthetic_ambiguous", {})
    async with repo.db.sessions.begin() as session:
        await session.execute(
            update(Observation)
            .where(Observation.namespace == store.namespace, Observation.external_id.in_(older_ids))
            .values(observed_at=func.now() - timedelta(hours=5))
        )
        for identity, age in [
            ("100", timedelta(hours=6)),
            (original_id, timedelta(minutes=3)),
            (orphan_id, timedelta(minutes=4)),
            ("200", timedelta(minutes=2)),
            ("201", timedelta(minutes=1)),
        ]:
            await session.execute(
                update(Observation)
                .where(
                    Observation.namespace == store.namespace, Observation.external_id == identity
                )
                .values(observed_at=func.now() - age)
            )

    async def observation_history():
        async with repo.db.sessions() as session:
            return list(
                await session.execute(
                    select(
                        Observation.id,
                        Observation.external_id,
                        Observation.observed_at,
                        Observation.payload,
                    )
                    .where(Observation.namespace == store.namespace)
                    .order_by(Observation.external_id)
                )
            )

    before = await store.summary()
    history = await observation_history()
    fresh = await store.unplanned(DISPATCHER, STRATEGY_VERSION, "new-prompt-version")
    assert fresh is not None and fresh.external_id == "200"
    generic = await store.unplanned("generic-account", STRATEGY_VERSION, PROMPT_VERSION)
    assert generic is not None and generic.external_id == "100"
    assert (await store.summary())["counts"] == before["counts"]
    assert await observation_history() == history
    assert len(history) == 20
    orphan = await store.find_observation(orphan_id)
    assert orphan is not None and orphan.payload["source"] == "news"
    assert await store.unknown() == [action]
    await store.skip(fresh.id, fresh.external_id, "Synthetic reviewed skip")
    following = await store.unplanned(DISPATCHER, STRATEGY_VERSION, PROMPT_VERSION)
    assert following is not None and following.external_id == "201"
    assert await store.unknown() == [action]
    assert await observation_history() == history
    await store.skip(following.id, following.external_id, "Synthetic reviewed skip")
    older = await store.unplanned(DISPATCHER, STRATEGY_VERSION, PROMPT_VERSION)
    assert older is not None and older.external_id == "100"
    assert await store.unknown() == [action]
    assert await observation_history() == history
    retained_orphan = await store.find_observation(orphan_id)
    assert retained_orphan is not None and retained_orphan.id == orphan.id
