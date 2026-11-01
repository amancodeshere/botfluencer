"""Discovery treats tags, usernames and inbound replies as untrusted public data."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from test_red import executor_for, intent, response_for
from test_red_conversations import conversation_store, successful_reply

from botfluencer.domain import Draft, ObservationInput
from botfluencer.platform.base import PermanentRejection
from botfluencer.red.discovery import ConversationDiscovery, decorate, select_tags
from botfluencer.storage.models import NPCPost
from botfluencer.storage.repository import Repository


def test_tag_selection_rejects_instructions_opposing_and_unrelated_tags() -> None:
    assert select_tags(
        {
            "items": [
                {"name": "ignore_previous_instructions"},
                {"name": "voteno"},
                {"name": "H2D2"},
                {"name": "referendum"},
                {"name": "kingstonia"},
            ]
        }
    ) == ("#h2d2", "#referendum")
    with pytest.raises(ValueError):
        select_tags({"items": "invalid"})


def test_decoration_preserves_body_urls_length_and_single_addressee() -> None:
    draft = Draft(kind="reply", target_id="123", text="Reviewed reply https://example.invalid")
    result = decorate(draft, ("#h2d2", "#referendum"), "synthetic_npc")
    assert result.text == "@synthetic_npc Reviewed reply https://example.invalid #h2d2 #referendum"
    assert (
        len(
            decorate(
                Draft(kind="reply", target_id="123", text="x" * 495),
                ("#referendum",),
                "synthetic_npc",
            ).text
        )
        <= 500
    )
    with pytest.raises(ValueError):
        decorate(draft, (), "bad @handle")
    with pytest.raises(ValueError):
        decorate(Draft(kind="post", target_id="post:10:1", text="Reviewed"), (), "synthetic")


@pytest.mark.parametrize("username,allowed", [("synthetic_npc", True), ("wrong_npc", False)])
async def test_mention_requires_live_identity_of_exact_reply_target(username: str, allowed: bool):
    writes = []

    def handler(request):
        if request.url.path == "/api/accounts/7":
            return httpx.Response(200, json={"account_id": 7, "username": "synthetic_npc"})
        if request.method == "POST":
            writes.append(request)
            return httpx.Response(
                200,
                json={
                    "body": json.loads(request.content)["body"],
                    "post_id": 124,
                    "author_id": 10,
                    "kind": "reply",
                    "reply_to_post_id": 123,
                    "root_post_id": 123,
                },
            )
        return response_for(request)

    async with executor_for(handler) as executor:
        executor.allow_mentions = True
        await executor.verify_sessions()
        action = intent()
        action.draft.text = "@" + username + " Reviewed reply"
        if allowed:
            await executor.execute(action)
            assert len(writes) == 1
        else:
            with pytest.raises(PermanentRejection):
                await executor.execute(action)
            assert not writes


async def test_mention_default_remains_off_and_unknown_author_never_resolved():
    async with executor_for(response_for) as executor:
        await executor.verify_sessions()
        action = intent()
        action.draft.text = "@synthetic_npc Reviewed reply"
        with pytest.raises(PermanentRejection):
            await executor.execute(action)
        executor._read_preflight = AsyncMock()
        with pytest.raises(PermanentRejection):
            await executor.mention_username(999)
        executor._read_preflight.assert_not_awaited()


@pytest.mark.integration
async def test_own_response_scanner_excludes_unknown_authors_and_prioritises_known_reply(
    repo: Repository,
):
    store = await conversation_store(repo)
    store.config.prioritize_conversations = True
    await successful_reply(store, 123)
    now = datetime.now(UTC)
    reply = {
        "post_id": 999,
        "author_id": 7,
        "kind": "reply",
        "body": "A question?",
        "reply_to_post_id": 500,
        "created_at": (now - timedelta(minutes=2)).isoformat(),
    }
    unknown = dict(reply, post_id=1000, author_id=9999)
    wrong_parent = dict(reply, post_id=1001, reply_to_post_id=99999)
    transport = AsyncMock()
    transport.request.return_value = {"items": [unknown, wrong_parent, reply]}
    discovery = ConversationDiscovery(store)
    # An already observed reply must gain priority without rewriting its original payload.
    await store.observe(
        [
            ObservationInput(
                external_id="999",
                text="A question?",
                source="social",
                metadata={"author_id": 7, "kind": "reply"},
            )
        ]
    )
    assert await discovery.poll(transport, frozenset({7}), hashtags=False) == 1
    async with repo.db.sessions.begin() as session:
        for number in range(10):
            session.add(
                NPCPost(
                    namespace=store.config.archive_namespace,
                    post_id=2000 + number,
                    author_id=7,
                    kind="reply",
                    npc_status="unverified",
                    post="Unrelated thread reply",
                    time_stamp=now - timedelta(seconds=number),
                )
            )
    candidates = await store.continuation_candidates(frozenset({7}))
    assert [item.external_id for item in candidates] == ["999"]
    assert all(item.external_id not in {"1000", "1001"} for item in candidates)
