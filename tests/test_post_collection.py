from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from botfluencer.competition.errors import RequestDeferred
from botfluencer.competition.transport import CompetitionTransport
from botfluencer.platform.collect import PostCollector
from botfluencer.storage.models import NPCPost
from botfluencer.storage.npc_posts import PostArchive, PostRecord
from botfluencer.storage.repository import Repository


def post(post_id: int = 1, *, body: str | None = "Synthetic post") -> dict[str, object]:
    return {
        "post_id": post_id,
        "author_id": 10,
        "body": body,
        "kind": "post",
        "created_at": "2026-10-19T17:40:00+11:00",
    }


def collector(
    responses: list[object], *, npc_before: datetime | None = None
) -> tuple[PostCollector, MagicMock, MagicMock]:
    transport = MagicMock(spec=CompetitionTransport)
    transport.request = AsyncMock(side_effect=responses)
    archive = MagicMock(spec=PostArchive)
    archive.namespace = "synthetic"
    archive.label_npc_before = AsyncMock(return_value=0)
    archive.save = AsyncMock(side_effect=lambda rows, **kwargs: len(rows))
    return PostCollector(transport, archive, npc_before=npc_before), transport, archive


async def test_collects_cursor_pages_and_empty_reposts_without_claiming_npc_identity() -> None:
    item, transport, archive = collector(
        [
            {"items": [post()], "next_cursor": "older"},
            {"items": [post(2, body=None)], "next_cursor": None},
            {
                "items": [
                    {
                        "account_id": 10,
                        "username": "synthetic",
                        "display_name": "Test",
                        "verified": True,
                    }
                ],
                "next_cursor": None,
            },
        ]
    )
    stats = await item.collect(10)
    assert stats["seen"] == stats["inserted"] == 2
    first = archive.save.call_args_list[2].args[0][0]
    assert first.user_name == "synthetic" and first.name == "Test"
    assert first.npc_status == "unverified"
    assert first.time_stamp.isoformat() == "2026-10-19T06:40:00+00:00"
    assert archive.save.call_args_list[1].args[0][0].post is None
    assert transport.request.call_args_list[1].kwargs["params"]["cursor"] == "older"
    assert all(call.args[0] == "social" for call in transport.request.call_args_list)


async def test_pacing_defer_retries_read_without_dropping_page() -> None:
    item, transport, _ = collector([RequestDeferred(0.01), {"items": [], "next_cursor": None}])
    assert [page async for page in item.pages("/posts", "feed", 2)] == [[]]
    assert transport.request.await_count == 2


async def test_repeated_cursor_cannot_claim_complete_archive() -> None:
    item, _, archive = collector(
        [
            {"items": [post()], "next_cursor": "same"},
            {"items": [post(2)], "next_cursor": "same"},
        ]
    )
    with pytest.raises(ValueError, match="repeated"):
        await item.collect(10)
    assert archive.save.await_count == 2


async def test_page_limit_and_missing_timestamp_fail_explicitly() -> None:
    item, _, _ = collector([{"items": [post()], "next_cursor": "more"}])
    with pytest.raises(RuntimeError, match="Page safety"):
        _ = [page async for page in item.pages("/posts", "feed", 1)]
    with pytest.raises(ValidationError):
        PostRecord.model_validate(
            {
                "post_id": 1,
                "author_id": 10,
                "user_name": None,
                "name": None,
                "post": "Test",
                "kind": "post",
                "time_stamp": "2026-10-19T17:40:00",
            }
        )


@pytest.mark.integration
async def test_archive_rerun_deduplicates_and_namespaces_remain_separate(repo: Repository) -> None:
    record = PostRecord(
        post_id=1,
        author_id=10,
        user_name="synthetic",
        name="Test",
        post="Synthetic post",
        time_stamp="2026-10-19T17:40:00+11:00",
        kind="post",
    )
    archive = PostArchive(repo.db, repo.namespace)
    assert await archive.save([record, record]) == 1
    assert await PostArchive(repo.db, repo.namespace).save([record]) == 0
    enriched = record.model_copy(update={"user_name": "changed", "post": "Must not replace body"})
    assert await archive.save([enriched, enriched], fill_names=True) == 1
    async with repo.db.sessions() as session:
        stored = await session.get(NPCPost, (repo.namespace, 1))
        assert stored is not None
        assert stored.post == "Synthetic post" and stored.user_name == "synthetic"

    assert await archive.label_npc_before(record.time_stamp) == 0
    assert await archive.label_npc_before(record.time_stamp + timedelta(minutes=20)) == 1
    other = PostArchive(repo.db, repo.namespace + "-other")
    assert await other.save([record]) == 1
    async with repo.db.sessions() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(NPCPost).where(NPCPost.namespace == repo.namespace)
            )
            == 1
        )
    with pytest.raises(ValidationError):
        PostRecord.model_validate(record.model_dump() | {"time_stamp": None})


async def test_team_confirmed_npc_cutoff_is_exclusive_and_timezone_aware() -> None:
    cutoff = datetime.fromisoformat("2026-10-19T18:00:00+11:00")
    at_cutoff = post(2) | {"created_at": cutoff.isoformat()}
    item, _, archive = collector(
        [
            {"items": [post(), at_cutoff], "next_cursor": None},
            {
                "items": [{"account_id": 10, "username": "synthetic", "display_name": "Test"}],
                "next_cursor": None,
            },
        ],
        npc_before=cutoff,
    )
    await item.collect(10)
    records = archive.save.call_args_list[0].args[0]
    assert records[0].npc_status == "npc"
    assert records[1].npc_status == "unverified"
    archive.label_npc_before.assert_awaited_once_with(cutoff)


def test_naive_npc_cutoff_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        collector([], npc_before=datetime(2026, 10, 19, 18))
