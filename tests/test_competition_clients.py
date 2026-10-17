import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from botfluencer.competition.errors import CompetitionHTTPError, InvalidGeneration, RequestDeferred
from botfluencer.competition.pacing import TeamPacer
from botfluencer.competition.standards import COMPETITION_OPENS, LLM_MODEL
from botfluencer.competition.transport import CompetitionTransport
from botfluencer.config import Settings
from botfluencer.domain import ActionIntent, Draft, ObservationInput
from botfluencer.llm.ctn import CTNGenerator
from botfluencer.platform.ctn import CTNNewsReader, CTNPlatform


@asynccontextmanager
async def transport_for(
    handler: Any, *, enabled: bool = True
) -> AsyncIterator[CompetitionTransport]:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://test@localhost/botfluencer_test",
        CTN_TEAM_CODE="TESTTEAM00000001",
        CTN_SIDE="Vote Yes",
        CTN_NETWORK_ENABLED=enabled,
    )
    # Mock only the pacing boundary for wire-contract tests. Durable pacing has
    # separate real-Postgres integration tests; there is no production fallback.
    pacer = MagicMock(spec=TeamPacer)
    pacer.scope = "synthetic-test"
    pacer.reserve = AsyncMock()
    pacer.observe = AsyncMock()
    pacer.database = MagicMock()

    @asynccontextmanager
    async def leadership(scope: str) -> AsyncIterator[MagicMock]:
        connection = MagicMock()
        connection.closed = connection.invalidated = False
        connection.execute = AsyncMock()
        yield connection

    pacer.database.leadership = leadership
    transport = CompetitionTransport(
        settings,
        pacer,
        http_transport=httpx.MockTransport(handler),
        clock=lambda: COMPETITION_OPENS,
    )
    try:
        yield transport
    finally:
        await transport.close()


async def test_feed_matches_sdk_contract_and_never_sends_team_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.host == "social.legitreal.com"
        assert request.url.path == "/api/posts"
        assert request.url.params["sort"] == "trending"
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "post_id": 42,
                        "author_id": 7,
                        "body": "Synthetic public post",
                        "root_post_id": 40,
                    },
                    {"post_id": 43, "body": None},
                ],
                "next_cursor": None,
            },
        )

    async with transport_for(handler) as transport:
        items = await CTNPlatform(transport).observe()
        assert len(items) == 1
        assert items[0].external_id == "42"
        assert items[0].source == "social"
        assert items[0].metadata["root_post_id"] == 40


async def test_429_preserves_retry_after_and_does_not_retry_in_transport() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            headers={
                "Retry-After": "180",
                "X-RateLimit-Limit": "3",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "60",
            },
            json={"error": "Not echoed"},
        )

    async with transport_for(handler) as transport:
        with pytest.raises(RequestDeferred) as raised:
            await CTNPlatform(transport).observe()
        assert raised.value.seconds == 180
        assert calls == 1
        transport.pacer.observe.assert_awaited_once_with(  # type: ignore[attr-defined]
            "feed", limit=3, remaining=0, reset=60.0, retry_after=180.0
        )


async def test_remote_access_is_opt_in_and_redirects_are_not_followed() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"Location": "https://outside.example"})

    async with transport_for(handler, enabled=False) as transport:
        with pytest.raises(RuntimeError, match="disabled"):
            await CTNPlatform(transport).observe()
    assert calls == 0
    async with transport_for(handler) as transport:
        with pytest.raises(CompetitionHTTPError) as raised:
            await CTNPlatform(transport).observe()
        assert raised.value.status == 302 and calls == 1


async def test_model_uses_only_team_key_and_validates_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-never-be-used")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "llm.legitreal.com"
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer TESTTEAM00000001"
        body = json.loads(request.content)
        assert body["model"] == LLM_MODEL and body["n"] == 1
        assert body["max_tokens"] == 150
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"target_id": "99", "text": "Synthetic reply"})
                        }
                    }
                ]
            },
        )

    async with transport_for(handler) as transport:
        with pytest.raises(InvalidGeneration):
            await CTNGenerator(transport).generate(
                ObservationInput(external_id="42", text="Synthetic")
            )


@pytest.mark.parametrize(
    "content",
    [
        "not JSON",
        '{"target_id":"42","text":""}',
        json.dumps({"target_id": "42", "text": "x" * 501}),
    ],
)
async def test_malformed_model_output_is_rejected(content: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with transport_for(handler) as transport:
        with pytest.raises(InvalidGeneration):
            await CTNGenerator(transport).generate(
                ObservationInput(external_id="42", text="Synthetic")
            )


async def test_valid_model_output_and_news_contracts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "llm.legitreal.com":
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": '{"target_id":"42","text":"Synthetic reply"}'}}
                    ]
                },
            )
        assert "authorization" not in request.headers
        assert request.url.path == "/index.json"
        return httpx.Response(200, json={"sites": [{"sub_url": "kingstonia-herald"}]})

    async with transport_for(handler) as transport:
        draft = await CTNGenerator(transport).generate(
            ObservationInput(external_id="42", text="Test")
        )
        assert draft == Draft(target_id="42", text="Synthetic reply")
        assert (await CTNNewsReader(transport).sites())[0]["sub_url"] == "kingstonia-herald"
        with pytest.raises(ValueError, match="sub_url"):
            await CTNNewsReader(transport).front_page("../../outside")


async def test_platform_has_no_live_executor() -> None:
    from uuid import uuid4

    async with transport_for(lambda r: httpx.Response(500)) as transport:
        intent = ActionIntent(
            id=uuid4(),
            key="test",
            account="test",
            draft=Draft(target_id="42", text="Test"),
            attempts=0,
        )
        with pytest.raises(RuntimeError, match="publishing is not enabled"):
            await CTNPlatform(transport).execute(intent)
        assert (await CTNPlatform(transport).reconcile(intent)).state == "unresolved"
