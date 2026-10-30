"""Archive topic filtering preserves policy checks and bounded discovery."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from botfluencer.domain import Draft, ObservationInput
from botfluencer.red.config import RedSettings
from botfluencer.red.policy import TOPIC_TERMS, eligible
from botfluencer.red.store import RedStore
from botfluencer.storage.npc_posts import PostArchive, PostRecord
from botfluencer.storage.repository import Repository


def policy_item(body: str) -> ObservationInput:
    return ObservationInput(
        external_id="100",
        text=body,
        source="social",
        metadata={
            "post_id": 100,
            "author_id": 7,
            "kind": "post",
            "created_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        },
    )


@pytest.mark.parametrize(
    "term", ["h2d2", "vaccine", "vaccination", "referendum", "mandate", "vote yes", "vote no"]
)
def test_reviewed_terms_keep_existing_case_insensitive_policy(term: str) -> None:
    assert term in TOPIC_TERMS
    assert eligible(
        policy_item(f"Synthetic {term.swapcase()} discussion"), frozenset({7}), datetime.now(UTC)
    )


@pytest.mark.parametrize("body", ["Café weather", "散步天气", "A sunny day ☀"])
def test_unicode_passthrough_does_not_establish_topic_eligibility(body: str) -> None:
    assert len(body.encode("utf-8")) != len(body)
    assert not eligible(policy_item(body), frozenset({7}), datetime.now(UTC))


@pytest.mark.integration
async def test_topic_prefilter_reaches_older_relevant_posts_and_keeps_discovery_guards(
    repo: Repository,
) -> None:
    repo.settings.accounts = ["10"]
    now = datetime.now(UTC)
    store = RedStore(repo.db)
    store.scope = "synthetic-discovery-" + uuid4().hex
    store.config = RedSettings(
        _env_file=None,
        expected_team_id=99,
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(hours=47),
        archive_namespace=repo.namespace,
        sessions_json='[{"account_id":10,"token":"fixture"}]',
    )
    await store.initialize()
    async with repo.db.sessions() as session:
        assert await session.scalar(text("SHOW server_encoding")) == "UTF8"
    await store.observe(
        [
            ObservationInput(external_id="400", text="Synthetic referendum"),
            ObservationInput(external_id="301", text="Previously observed referendum"),
        ]
    )
    target = await store.find_observation("400")
    assert target is not None
    assert await store.plan_reply(target.id, 12, "10", Draft(target_id="400", text="Fixture reply"))

    def record(
        identity, body="Synthetic referendum", *, author=7, seconds=60, kind="post", status="npc"
    ):
        return PostRecord(
            post_id=identity,
            author_id=author,
            user_name=None,
            name=None,
            post=body,
            time_stamp=now - timedelta(seconds=seconds),
            kind=kind,
            npc_status=status,
        )

    archive = PostArchive(repo.db, repo.namespace)
    await archive.save(
        [
            *[record(identity, "Synthetic weather update") for identity in range(101, 109)],
            record(90, "Older ReFeReNdUm discussion", seconds=120),
            record(91, "Café weather", author=8, seconds=120),
            record(300, seconds=5 * 3600),
            record(301),
            record(302, kind="reply"),
            record(303, seconds=-3600),
            record(304, author=9, status="player"),
            record(305, author=10),
            record(306, author=11, status="unverified"),
            record(307, author=12),
        ]
    )
    await PostArchive(repo.db, repo.namespace + "-other").save([record(308)])
    npc_ids = await store.npc_ids()
    assert npc_ids == frozenset({7, 8, 12})
    first = await store.candidates(npc_ids)
    assert [item.external_id for item in first] == ["91", "90"]
    assert not eligible(first[0], npc_ids, datetime.now(UTC))
    assert eligible(first[1], npc_ids, datetime.now(UTC))
    assert all("root_post_id" not in item.metadata for item in first)
    await archive.save(
        [
            record(200 + index, f"Mixed case {term.swapcase()} discussion", seconds=30)
            for index, term in enumerate(TOPIC_TERMS)
        ]
    )
    bounded = await store.candidates(npc_ids)
    assert [item.external_id for item in bounded] == [
        *[str(identity) for identity in range(206, 199, -1)],
        "91",
    ]
    assert len(bounded) == 8
    assert all(eligible(item, npc_ids, datetime.now(UTC)) for item in bounded[:7])
    await store.observe(bounded)
    remaining = await store.candidates(npc_ids)
    assert [item.external_id for item in remaining] == ["90"]
    assert await store.candidates(frozenset()) == []
    assert (await store.summary())["counts"]["actions"] == 1
