"""Planner tests for small, bounded trends engagement preparation."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from pydantic import SecretStr

from botfluencer.red.config import RedSettings
from botfluencer.red.engagement_planner import EngagementPlanner
from botfluencer.red.engagement_policy import TEAM_REPLY_TEXTS, body_digest
from botfluencer.red.worker import RedWorker

NOW = datetime.now(UTC)
BODY = "Vote Yes on the referendum for clear safeguards and public oversight."


def config(**updates) -> RedSettings:
    values = dict(
        _env_file=None,
        expected_team_id=99,
        starts_at=NOW - timedelta(minutes=1),
        ends_at=NOW + timedelta(hours=12),
        max_attempts=20,
        archive_namespace="synthetic-archive",
        sessions_json=SecretStr('[{"account_id":10,"token":"synthetic-token"}]'),
    )
    values.update(updates)
    return RedSettings(**values)


def candidate(
    target_id: int,
    *,
    author_id: int = 7,
    target_type: str = "npc",
    healthy_accounts: list[str] | None = None,
    reserved: dict | None = None,
) -> dict:
    return {
        "target_id": target_id,
        "author_id": author_id,
        "target_type": target_type,
        "body_hash": body_digest(BODY),
        "source_action_id": uuid4() if target_type == "own" else None,
        "healthy_accounts": healthy_accounts or ["10", "11"],
        "reserved": reserved or {},
        "created_at": NOW + timedelta(seconds=target_id),
    }


def planner_for(*, config_override=None, candidates=None, trends=None):
    store = MagicMock()
    store.is_paused = AsyncMock(return_value=False)
    store.record_trending_snapshot = AsyncMock()
    store.own_candidates = AsyncMock(return_value=candidates or [])
    store.npc_candidates = AsyncMock(return_value=candidates or [])
    store.reserve_engagement = AsyncMock(return_value=uuid4())
    transport = MagicMock()
    transport.clock = lambda: NOW
    transport.request = AsyncMock(
        return_value={"items": [{"post_id": value} for value in (trends or [])]}
    )
    return (
        EngagementPlanner(store, transport, config_override or config(engagement_enabled=True)),
        store,
        transport,
    )


async def test_default_disabled_planner_makes_no_network_or_store_calls() -> None:
    planner, store, transport = planner_for(config_override=config())
    assert await planner.prepare(own_only=False) is False
    transport.request.assert_not_awaited()
    store.is_paused.assert_not_awaited()
    store.record_trending_snapshot.assert_not_awaited()
    store.npc_candidates.assert_not_awaited()


async def test_trending_snapshot_stores_only_exact_ranked_identifiers_and_skips_matches() -> None:
    items = [candidate(21), candidate(22)]
    planner, store, transport = planner_for(candidates=items, trends=[21, 22])
    assert await planner.prepare(own_only=False) is False
    transport.request.assert_awaited_once_with(
        "social", "/posts", "feed", params={"sort": "trending", "limit": 20}
    )
    store.record_trending_snapshot.assert_awaited_once()
    ids, observed_at = store.record_trending_snapshot.await_args.args
    assert ids == [21, 22]
    assert observed_at.tzinfo is not None
    store.reserve_engagement.assert_not_awaited()


async def test_own_candidates_are_sorted_and_one_prepare_reserves_at_most_one_action() -> None:
    planner, store, _ = planner_for(
        candidates=[candidate(32, target_type="own"), candidate(31, target_type="own")]
    )
    assert await planner.prepare(own_only=True) is True
    store.reserve_engagement.assert_awaited_once()
    args = store.reserve_engagement.await_args
    assert args.args[1] == 31
    assert args.args[2] == "like"
    assert args.kwargs["body_hash"] == body_digest(BODY)


async def test_actor_selection_excludes_post_author_and_avoids_existing_reservers() -> None:
    planned = AsyncMock(return_value=uuid4())
    planner, _, _ = planner_for(
        config_override=config(
            engagement_enabled=True,
            engagement_likes_per_target=3,
            engagement_reposts_per_target=0,
            engagement_comments_per_target=0,
        )
    )
    planner.store.reserve_engagement = planned
    item = candidate(
        41,
        author_id=10,
        healthy_accounts=["10", "11", "12"],
        reserved={"like": {"count": 1, "actors": ["12"]}},
    )
    assert await planner._reserve_candidate(item) is True
    assert planned.await_args.args[0] == "11"
    assert planned.await_args.args[0] != str(item["author_id"])
    assert planned.await_args.args[2] == "like"


async def test_team_reply_is_only_considered_for_own_posts_after_first_like_stage() -> None:
    planner, store, _ = planner_for()
    planner.config = config(
        engagement_enabled=True,
        engagement_likes_per_target=1,
        engagement_reposts_per_target=0,
        engagement_comments_per_target=1,
    )
    own = candidate(
        51,
        author_id=10,
        target_type="own",
        reserved={"like": {"count": 1, "actors": ["11"]}},
    )
    assert await planner._reserve_candidate(own) is True
    assert store.reserve_engagement.await_args.args[2] == "team_reply"
    assert store.reserve_engagement.await_args.kwargs["text"] in TEAM_REPLY_TEXTS

    npc = candidate(52, reserved={"like": {"count": 1, "actors": ["10", "11"]}})
    planner.config = config(
        engagement_enabled=True,
        engagement_likes_per_target=0,
        engagement_reposts_per_target=0,
        engagement_comments_per_target=1,
    )
    store.reserve_engagement.reset_mock()
    assert await planner._reserve_candidate(npc) is False
    store.reserve_engagement.assert_not_awaited()


async def test_worker_gives_own_lane_first_and_support_lane_only_after_editorial() -> None:
    calls = []

    class Planner:
        async def prepare(self, *, own_only: bool) -> bool:
            calls.append("own" if own_only else "support")
            return False

    worker = object.__new__(RedWorker)
    worker.settings = SimpleNamespace(operation_timeout_seconds=1)
    worker.config = config(engagement_enabled=True)
    worker.engagement_planner = Planner()
    worker._next_engagement_reconciliation = float("inf")

    async def editorial() -> bool:
        calls.append("editorial")
        return False

    worker._plan_editorial = editorial
    await worker.plan_contextual()
    assert calls == ["own", "editorial", "support"]


async def test_engagement_planner_routes_tick_through_contextual_worker() -> None:
    worker = object.__new__(RedWorker)
    worker.config = config(use_model=False)
    worker.engagement_planner = object()
    worker.contextual_tick = AsyncMock()
    await worker.tick()
    worker.contextual_tick.assert_awaited_once_with()
