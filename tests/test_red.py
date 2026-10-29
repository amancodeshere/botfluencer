import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from botfluencer.competition.errors import CompetitionHTTPError, RequestDeferred
from botfluencer.competition.pacing import TeamPacer
from botfluencer.competition.standards import COMPETITION_OPENS
from botfluencer.competition.transport import CompetitionTransport
from botfluencer.config import Settings
from botfluencer.domain import ActionIntent, Draft
from botfluencer.platform.base import PermanentRejection, SafeRetry
from botfluencer.red.executor import RedSession, ReplyExecutor


def intent(target: str = "123") -> ActionIntent:
    return ActionIntent(
        id=uuid4(),
        key="synthetic-red",
        account="10",
        attempts=1,
        draft=Draft(target_id=target, text="Synthetic simulation reply"),
    )


@asynccontextmanager
async def executor_for(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    enabled: bool = True,
    network: bool = True,
    clock: Callable[[], datetime] = lambda: COMPETITION_OPENS,
) -> AsyncIterator[ReplyExecutor]:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://test@localhost/botfluencer_test",
        accounts=["10"],
        CTN_TEAM_CODE="TESTTEAM00000001",
        CTN_SIDE="Vote Yes",
        CTN_NETWORK_ENABLED=network,
    )
    pacer = MagicMock(spec=TeamPacer)
    pacer.reserve = AsyncMock()
    pacer.observe = AsyncMock()
    transport = CompetitionTransport(
        settings,
        pacer,
        http_transport=httpx.MockTransport(handler),
        clock=clock,
    )
    executor = ReplyExecutor(
        transport,
        {"10": RedSession(10, SecretStr("synthetic-session-token"))},
        npc_author_ids=frozenset({7}),
        expected_team_id=99,
        ends_at=COMPETITION_OPENS + timedelta(hours=48),
        enabled=enabled,
    )
    try:
        yield executor
    finally:
        await transport.close()


def response_for(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/feed/lock":
        return httpx.Response(200, json={"locked": False, "reason": None})
    if request.url.path == "/api/auth/me":
        assert request.headers["Authorization"] == "Bearer synthetic-session-token"
        return httpx.Response(200, json={"account": {"account_id": 10}, "team": {"team_id": 99}})
    if request.method == "GET":
        assert request.url.path == "/api/posts/123"
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={"post_id": 123, "author_id": 7, "kind": "post", "root_post_id": 123},
        )
    assert request.method == "POST" and request.url.path == "/api/posts"
    assert request.headers["Authorization"] == "Bearer synthetic-session-token"
    assert json.loads(request.content) == {
        "kind": "reply",
        "body": intent().draft.text,
        "reply_to_post_id": 123,
        "root_post_id": 123,
    }
    return httpx.Response(
        200,
        json={
            "post_id": 124,
            "author_id": 10,
            "kind": "reply",
            "body": intent().draft.text,
            "reply_to_post_id": 123,
            "root_post_id": 123,
        },
    )


async def test_reply_wire_contract_uses_account_auth_and_durable_publish_bucket() -> None:
    async with executor_for(response_for) as executor:
        await executor.verify_sessions()
        receipt = await executor.execute(intent())
        assert receipt.external_id == "124"
        assert receipt.details == {"target_id": "123"}
        assert [call.args[0] for call in executor.transport.pacer.reserve.await_args_list] == [
            "other_read",
            "other_read",
            "other_read",
            "publish",
        ]
        assert (await executor.reconcile(intent())).state == "unresolved"


@pytest.mark.parametrize("enabled,network", [(False, True), (True, False)])
async def test_reply_requires_both_explicit_opt_ins(enabled: bool, network: bool) -> None:
    calls = []
    async with executor_for(
        lambda r: calls.append(r), enabled=enabled, network=network
    ) as executor:
        with pytest.raises(PermanentRejection):
            await executor.verify_sessions()
        with pytest.raises(PermanentRejection):
            await executor.execute(intent())
    assert not calls


@pytest.mark.parametrize("offset", [-1, 48 * 3600, 49 * 3600])
async def test_opening_and_deadline_reject_without_network(offset: int) -> None:
    calls = []
    async with executor_for(
        lambda r: calls.append(r),
        clock=lambda: COMPETITION_OPENS + timedelta(seconds=offset),
    ) as executor:
        with pytest.raises(PermanentRejection):
            await executor.verify_sessions()
    assert not calls


async def test_deadline_is_rechecked_after_target_preflight() -> None:
    now = COMPETITION_OPENS
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal now
        calls.append(request)
        if request.url.path == "/api/posts/123":
            now += timedelta(hours=48)
        return response_for(request)

    async with executor_for(handler, clock=lambda: now) as executor:
        await executor.verify_sessions()
        with pytest.raises(PermanentRejection):
            await executor.execute(intent())
    assert all(request.method == "GET" for request in calls)


@pytest.mark.parametrize("account,team", [(11, 99), (10, 98), (True, 99), (10, False)])
async def test_session_identity_mismatch_cannot_publish(account: Any, team: Any) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json={"account": {"account_id": account}, "team": {"team_id": team}}
        )

    async with executor_for(handler) as executor:
        with pytest.raises(ValueError):
            await executor.verify_sessions()
        with pytest.raises(PermanentRejection):
            await executor.execute(intent())
    assert len(calls) == 1


@pytest.mark.parametrize("target", ["0", "0123", "-1", "../123", "１２３"])
async def test_target_identifier_cannot_change_route(target: str) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response_for(request)

    async with executor_for(handler) as executor:
        await executor.verify_sessions()
        with pytest.raises(PermanentRejection):
            await executor.execute(intent(target))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "target",
    [
        {"post_id": 123, "author_id": 8},
        {"post_id": 123, "author_id": 10},
        {"post_id": 122, "author_id": 7},
        {"post_id": 123, "author_id": 7, "kind": "post", "root_post_id": 0},
        {"post_id": 123, "author_id": 7, "kind": "post", "root_post_id": False},
        {"post_id": 123, "author_id": 7, "kind": "reply"},
        {"post_id": 123, "author_id": 7, "kind": "post", "root_post_id": 999},
        {"post_id": 123, "author_id": 7, "kind": "post", "reply_to_post_id": 999},
    ],
)
async def test_only_confirmed_npc_posts_with_valid_roots_can_be_targets(target: dict) -> None:
    writes = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            writes.append(request)
        if request.url.path == "/api/posts/123":
            return httpx.Response(200, json=target)
        return response_for(request)

    async with executor_for(handler) as executor:
        await executor.verify_sessions()
        with pytest.raises(PermanentRejection):
            await executor.execute(intent())
    assert not writes


@pytest.mark.parametrize("failure", ["timeout", "408", "500", "redirect", "bad_json", "bad_author"])
async def test_uncertain_publication_is_not_classified_as_safe_retry(failure: str) -> None:
    writes = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return response_for(request)
        writes.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("Synthetic ambiguity")
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://outside.invalid"})
        if failure == "bad_json":
            return httpx.Response(200, text="not JSON")
        if failure == "bad_author":
            body = response_for(request).json()
            body["author_id"] = 11
            return httpx.Response(200, json=body)
        return httpx.Response(int(failure))

    async with executor_for(handler) as executor:
        await executor.verify_sessions()
        with pytest.raises((httpx.TransportError, CompetitionHTTPError, ValueError)):
            await executor.execute(intent())
        assert (await executor.reconcile(intent())).state == "unresolved"
    assert len(writes) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_definitive_publication_rejection_is_permanent(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status) if request.method == "POST" else response_for(request)

    async with executor_for(handler) as executor:
        await executor.verify_sessions()
        with pytest.raises(PermanentRejection):
            await executor.execute(intent())


async def test_publication_throttle_preserves_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(429, headers={"Retry-After": "180"})
        return response_for(request)

    async with executor_for(handler) as executor:
        await executor.verify_sessions()
        with pytest.raises(SafeRetry) as raised:
            await executor.execute(intent())
        assert raised.value.retry_after_seconds == 180


async def test_budget_deferral_prevents_publication() -> None:
    writes = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            writes.append(request)
        return response_for(request)

    async with executor_for(handler) as executor:
        await executor.verify_sessions()
        executor.transport.pacer.reserve.side_effect = [None, None, RequestDeferred(60)]
        with pytest.raises(SafeRetry):
            await executor.execute(intent())
    assert not writes


async def test_pacing_storage_failure_after_publication_remains_ambiguous() -> None:
    async with executor_for(response_for) as executor:
        await executor.verify_sessions()
        executor.transport.pacer.observe.side_effect = [
            None,
            None,
            RuntimeError("Synthetic DB outage"),
        ]
        with pytest.raises(RuntimeError, match="Synthetic DB outage"):
            await executor.execute(intent())
        assert (await executor.reconcile(intent())).state == "unresolved"


@pytest.mark.parametrize("text", ["Hello @npc", "Contact x@example.invalid"])
async def test_mentions_are_rejected_before_target_read(text: str) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response_for(request)

    async with executor_for(handler) as executor:
        await executor.verify_sessions()
        action = intent()
        action.draft.text = text
        with pytest.raises(PermanentRejection):
            await executor.execute(action)
    assert len(calls) == 1


async def test_expired_session_cannot_publish_after_successful_verification() -> None:
    now = COMPETITION_OPENS
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response_for(request)

    async with executor_for(handler, clock=lambda: now) as executor:
        executor.sessions["10"] = RedSession(
            10,
            SecretStr("synthetic-session-token"),
            COMPETITION_OPENS + timedelta(minutes=5),
        )
        await executor.verify_sessions()
        now += timedelta(minutes=5)
        with pytest.raises(PermanentRejection):
            await executor.execute(intent())
    assert len(calls) == 1


async def test_session_expiry_is_rechecked_after_preflight() -> None:
    now = COMPETITION_OPENS
    writes = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal now
        if request.method == "POST":
            writes.append(request)
        if request.url.path == "/api/posts/123":
            now += timedelta(minutes=5)
        return response_for(request)

    async with executor_for(handler, clock=lambda: now) as executor:
        executor.sessions["10"] = RedSession(
            10,
            SecretStr("synthetic-session-token"),
            COMPETITION_OPENS + timedelta(minutes=5),
        )
        await executor.verify_sessions()
        with pytest.raises(PermanentRejection):
            await executor.execute(intent())
    assert not writes


@pytest.mark.parametrize("hours", [0, -1, 49])
def test_campaign_rejects_unbounded_or_invalid_windows(hours: int) -> None:
    from pydantic import ValidationError

    from botfluencer.red.config import RedSettings

    with pytest.raises(ValidationError):
        RedSettings(
            _env_file=None,
            expected_team_id=99,
            starts_at=COMPETITION_OPENS,
            ends_at=COMPETITION_OPENS + timedelta(hours=hours),
            sessions_json=SecretStr('[{"account_id":10,"token":"synthetic-token"}]'),
        )


@pytest.mark.parametrize(
    "entries",
    [
        [{"account_id": 11, "token": "synthetic-token"}],
        [{"account_id": True, "token": "synthetic-token"}],
        [{"account_id": 10, "token": "synthetic-token"}] * 2,
    ],
)
def test_campaign_session_ids_must_exactly_match_configured_accounts(entries: list) -> None:
    from botfluencer.red.config import RedSettings

    config = RedSettings(
        _env_file=None,
        expected_team_id=99,
        starts_at=COMPETITION_OPENS,
        ends_at=COMPETITION_OPENS + timedelta(hours=48),
        sessions_json=SecretStr(json.dumps(entries)),
    )
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://test@localhost/botfluencer_test",
        accounts=["10"],
        platform_backend="ctn",
        mode="observe",
        CTN_NETWORK_ENABLED=True,
        CTN_TEAM_CODE="TESTTEAM00000001",
        CTN_SIDE="Vote Yes",
        CTN_MAX_TOTAL_ACCOUNTS=4,
        CTN_MAX_ACTIVE_ACCOUNTS=4,
    )
    with pytest.raises(ValueError):
        config.sessions(settings)


@pytest.mark.parametrize("outcome", ["success", "timeout", "malformed", "rejected", "wrong_team"])
async def test_registration_private_recovery_and_no_retry(
    tmp_path, monkeypatch, outcome: str
) -> None:
    import stat
    from types import SimpleNamespace

    import botfluencer.red.provision as module

    path = tmp_path / "accounts.json"
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        saved = json.loads(path.read_text())
        if request.method == "POST":
            assert saved["accounts"][0]["status"] == "registering"
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            payload = json.loads(request.content)
            assert payload["team_key"] == "TESTTEAM00000001"
            assert payload["username"].startswith("ctnsim_")
            if outcome == "timeout":
                raise httpx.ReadTimeout("Synthetic registration ambiguity")
            if outcome == "malformed":
                return httpx.Response(200, json={})
            if outcome == "rejected":
                return httpx.Response(403)
            return httpx.Response(
                200, json={"account": {"account_id": 10}, "token": "synthetic-token"}
            )
        # Credentials must already be recoverable before verification starts.
        assert saved["accounts"][0]["status"] == "registered"
        assert saved["accounts"][0]["token"] == "synthetic-token"
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        return httpx.Response(
            200,
            json={
                "account": {"account_id": 10},
                "team": {"team_id": 98 if outcome == "wrong_team" else 99},
            },
        )

    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://test@localhost/botfluencer_test",
        CTN_TEAM_CODE="TESTTEAM00000001",
        CTN_NETWORK_ENABLED=True,
        CTN_MAX_TOTAL_ACCOUNTS=4,
        CTN_MAX_ACTIVE_ACCOUNTS=4,
    )
    database = SimpleNamespace(settings=settings, close=AsyncMock())
    pacer = SimpleNamespace(reserve=AsyncMock(), observe=AsyncMock())
    monkeypatch.setattr(module, "Database", lambda _: database)
    monkeypatch.setattr(module, "TeamPacer", lambda _: pacer)
    monkeypatch.setattr(module, "require_open", lambda *args: None)
    monkeypatch.setattr(
        module,
        "CompetitionTransport",
        lambda settings, pacer: CompetitionTransport(
            settings,
            pacer,
            http_transport=httpx.MockTransport(handler),
            clock=lambda: COMPETITION_OPENS,
        ),
    )
    if outcome == "success":
        summary = await module.provision(settings, path, 1, expected_team_id=99)
        assert summary == {"requested": 1, "registered": 1, "verified": 1, "account_ids": [10]}
    else:
        with pytest.raises(RuntimeError, match="inspect the private recovery file"):
            await module.provision(settings, path, 1, expected_team_id=99)
    assert sum(request.method == "POST" for request in requests) == 1
    saved = json.loads(path.read_text())
    assert (
        saved["accounts"][0]["status"]
        == {
            "success": "verified",
            "timeout": "unknown",
            "malformed": "unknown",
            "rejected": "rejected",
            "wrong_team": "registered",
        }[outcome]
    )
    database.close.assert_awaited_once()
    with pytest.raises(ValueError, match="already exists"):
        await module.provision(settings, path, 1, expected_team_id=99)
    assert sum(request.method == "POST" for request in requests) == 1
