"""Postgres coverage for filtered red action claims and shared budgets."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr

from botfluencer.domain import Draft, ObservationInput
from botfluencer.red.config import RedSettings
from botfluencer.red.store import RedStore
from botfluencer.storage.models import RedCampaign
from botfluencer.storage.repository import Repository


def red_config(namespace: str, *, max_attempts: int = 10) -> RedSettings:
    now = datetime.now(UTC)
    return RedSettings(
        _env_file=None,
        expected_team_id=99,
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(hours=12),
        max_attempts=max_attempts,
        archive_namespace=namespace,
        sessions_json=SecretStr('[{"account_id":10,"token":"synthetic-token"}]'),
        engagement_enabled=True,
        parallel_lanes=True,
    )


@pytest.mark.integration
async def test_concurrent_filtered_claims_are_disjoint_and_pending_counts_are_lane_scoped(
    repo: Repository,
) -> None:
    repo.settings.accounts = ["10"]
    store = RedStore(repo.db)
    store.scope = "lane-" + uuid4().hex
    store.config = red_config("archive-" + uuid4().hex)
    await store.initialize()

    for identity, author, kind in ((201, 7, "reply"), (202, 8, "post")):
        item = ObservationInput(
            external_id=str(identity),
            text="Synthetic referendum topic",
            source="social",
            metadata={"author_id": author, "kind": "post"},
        )
        await store.observe([item])
        observed = await store.find_observation(str(identity))
        assert observed is not None
        assert await store.plan_reply(
            observed.id,
            author,
            "10",
            Draft(kind=kind, target_id=str(identity), text="Synthetic reviewed reply"),
        )

    assert await store.pending_count() == 2
    assert await store.pending_count(kinds=("reply",)) == 1
    assert await store.pending_count(kinds=("post",)) == 1

    reply_claim, post_claim = await asyncio.gather(
        store.claim(kinds=("reply",)),
        store.claim(kinds=("post",)),
    )
    if reply_claim is None:
        reply_claim = await store.claim(kinds=("reply",))
    if post_claim is None:
        post_claim = await store.claim(kinds=("post",))
    assert reply_claim is not None and reply_claim.draft.kind == "reply"
    assert post_claim is not None and post_claim.draft.kind == "post"
    assert reply_claim.id != post_claim.id
    assert await store.pending_count() == 2
    assert await store.pending_count(kinds=("reply",)) == 1
    assert await store.pending_count(kinds=("post",)) == 1


@pytest.mark.integration
async def test_lane_filters_share_hourly_and_campaign_attempt_caps(repo: Repository) -> None:
    repo.settings.accounts = ["10"]
    repo.settings.max_actions_per_hour = 20
    store = RedStore(repo.db)
    store.scope = "lane-cap-" + uuid4().hex
    store.config = red_config("archive-" + uuid4().hex, max_attempts=1)
    await store.initialize()

    for identity, author, kind in ((301, 17, "like"), (302, 18, "post")):
        await store.observe(
            [
                ObservationInput(
                    external_id=str(identity),
                    text="Synthetic referendum topic",
                    source="social",
                    metadata={"author_id": author, "kind": "post"},
                )
            ]
        )
        observed = await store.find_observation(str(identity))
        assert observed is not None
        assert await store.plan_reply(
            observed.id,
            author,
            "10",
            Draft(
                kind=kind,
                target_id=str(identity),
                text="" if kind == "like" else "Synthetic reviewed reply",
            ),
        )

    claims = await asyncio.gather(
        store.claim(kinds=("like",)),
        store.claim(kinds=("post",)),
    )
    claimed = [claim for claim in claims if claim is not None]
    assert len(claimed) == 1
    assert await store.claim() is None
    assert await store.pending_count() == 2

    # Raise only the campaign ceiling; the already consumed hourly slot remains shared.
    async with repo.db.sessions.begin() as session:
        campaign = await session.get(RedCampaign, store.scope)
        assert campaign is not None
        campaign.max_attempts = 10
    repo.settings.max_actions_per_hour = 1
    assert await store.claim() is None


@pytest.mark.integration
async def test_parallel_planning_reserves_capacity_for_each_lane(repo: Repository) -> None:
    repo.settings.accounts = ["10"]
    store = RedStore(repo.db)
    store.scope = "lane-capacity-" + uuid4().hex
    store.config = red_config("archive-" + uuid4().hex).model_copy(
        update={"max_pending_actions": 4}
    )
    await store.initialize()

    async def plan(identity: int, kind: str) -> bool:
        await store.observe(
            [
                ObservationInput(
                    external_id=str(identity),
                    text="Synthetic referendum topic",
                    source="social",
                    metadata={"author_id": identity, "kind": "post"},
                )
            ]
        )
        observed = await store.find_observation(str(identity))
        assert observed is not None
        return await store.plan_reply(
            observed.id,
            identity,
            "10",
            Draft(
                kind=kind,
                target_id=str(identity),
                text="" if kind == "like" else "Synthetic reviewed reply",
            ),
        )

    assert await plan(401, "post")
    assert await plan(402, "post")
    # The publication lane cannot consume the two slots reserved for engagement.
    assert not await plan(403, "post")
    assert await plan(404, "like")
    assert await plan(405, "like")
    assert not await plan(406, "like")
    assert await store.pending_count(kinds=("post",)) == 2
    assert await store.pending_count(kinds=("like",)) == 2


@pytest.mark.integration
async def test_busy_campaign_defers_parallel_claim_and_planning_without_waiting(repo: Repository):
    from sqlalchemy import select

    from botfluencer.competition.errors import RequestDeferred

    repo.settings.accounts = ["10"]
    store = RedStore(repo.db)
    store.scope = "busy-lane-" + uuid4().hex
    store.config = red_config("archive-" + uuid4().hex)
    await store.initialize()
    await store.observe([ObservationInput(external_id="701", text="Synthetic", source="social")])
    observation = await store.find_observation("701")
    assert observation is not None
    async with repo.db.sessions.begin() as session:
        await session.scalar(
            select(RedCampaign).where(RedCampaign.scope == store.scope).with_for_update()
        )
        async with asyncio.timeout(8):
            assert await store.claim(kinds=("post",)) is None
            with pytest.raises(RequestDeferred):
                await store.plan_reply(
                    observation.id,
                    0,
                    "10",
                    Draft(kind="post", target_id="post:10:1", text="Synthetic"),
                )
    assert await store.pending_count() == 0
