"""Replacement safeguards use synthetic identities and mocked organiser requests."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, update

from botfluencer.competition.standards import COMPETITION_OPENS
from botfluencer.competition.transport import CompetitionTransport
from botfluencer.config import Settings
from botfluencer.domain import ActionStatus, Draft, ObservationInput
from botfluencer.red.config import RedSettings
from botfluencer.red.executor import RedSession, ReplyExecutor
from botfluencer.red.store import RedStore
from botfluencer.storage.models import RedCampaign, RedCampaignRevision
from botfluencer.storage.npc_posts import PostArchive, PostRecord
from botfluencer.storage.repository import Repository


def provision_settings() -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_URL="postgresql://fixture@localhost/botfluencer_test",
        CTN_TEAM_CODE="TESTTEAM00000001",
        CTN_NETWORK_ENABLED=True,
        CTN_MAX_TOTAL_ACCOUNTS=200,
        CTN_MAX_ACTIVE_ACCOUNTS=50,
    )


def fake_provision(monkeypatch, handler, *, deadline_rows=None):
    import botfluencer.red.provision as module

    settings = provision_settings()
    rows = iter(deadline_rows or [None] * 20)
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: next(rows))
        )
    )

    @asynccontextmanager
    async def sessions():
        yield session

    database = SimpleNamespace(settings=settings, sessions=sessions, close=AsyncMock())
    pacer = SimpleNamespace(scope="synthetic-team", reserve=AsyncMock(), observe=AsyncMock())
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
    return module, settings, database, pacer


@pytest.mark.parametrize(
    "total,active,count",
    [
        (None, 41, 9),
        (50, None, 9),
        (True, 41, 9),
        (50, False, 9),
        (50.0, 41, 9),
        (50, "41", 9),
        (-1, 0, 1),
        (50, -1, 1),
        (40, 41, 9),
        (201, 41, 1),
        (50, 51, 1),
        (192, 41, 9),
        (50, 42, 9),
    ],
)
async def test_unknown_invalid_or_insufficient_snapshot_prevents_file_and_network(
    tmp_path, monkeypatch, total, active, count
) -> None:
    import botfluencer.red.provision as module

    database = Mock(side_effect=AssertionError("Invalid snapshot must precede database access"))
    transport = Mock(side_effect=AssertionError("Invalid snapshot must precede network access"))
    monkeypatch.setattr(module, "Database", database)
    monkeypatch.setattr(module, "CompetitionTransport", transport)
    path = tmp_path / "invalid.json"
    with pytest.raises(ValueError):
        await module.provision(
            provision_settings(),
            path,
            count,
            expected_team_id=99,
            current_total=total,
            current_active=active,
        )
    assert not path.exists()
    database.assert_not_called()
    transport.assert_not_called()


async def test_expired_campaign_blocks_provision_before_recovery_file(
    tmp_path, monkeypatch
) -> None:
    requests = []
    now = datetime.now(UTC)
    module, settings, database, pacer = fake_provision(
        monkeypatch,
        lambda request: requests.append(request) or httpx.Response(500),
        deadline_rows=[(now - timedelta(seconds=1), now)],
    )
    path = tmp_path / "expired.json"
    with pytest.raises((RuntimeError, ValueError)):
        await module.provision(
            settings, path, 9, expected_team_id=99, current_total=50, current_active=41
        )
    assert not path.exists() and not requests
    pacer.reserve.assert_not_awaited()
    database.close.assert_awaited_once()


@pytest.mark.parametrize("failure", ["timeout", "malformed", "rejected"])
async def test_partial_provision_stops_and_preserves_acknowledged_credentials(
    tmp_path, monkeypatch, failure: str
) -> None:
    requests = []
    registrations = 0
    path = tmp_path / "partial.json"

    def handler(request):
        nonlocal registrations
        requests.append(request)
        if request.method == "POST":
            registrations += 1
            if registrations == 2:
                if failure == "timeout":
                    raise httpx.ReadTimeout("Synthetic ambiguity")
                return httpx.Response(200 if failure == "malformed" else 403, json={})
            return httpx.Response(
                200, json={"account": {"account_id": 60}, "token": "fixture-token"}
            )
        saved = json.loads(path.read_text())
        assert saved["accounts"][0]["token"] == "fixture-token"
        return httpx.Response(200, json={"account": {"account_id": 60}, "team": {"team_id": 99}})

    module, settings, database, _ = fake_provision(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="private recovery file"):
        await module.provision(
            settings, path, 3, expected_team_id=99, current_total=50, current_active=41
        )
    saved = json.loads(path.read_text())
    assert saved["current_total"] == 50 and saved["current_active"] == 41
    assert [entry["status"] for entry in saved["accounts"]] == [
        "verified",
        "rejected" if failure == "rejected" else "unknown",
        "planned",
    ]
    assert saved["accounts"][0]["account_id"] == 60
    assert saved["accounts"][0]["token"] == "fixture-token"
    assert registrations == 2 and len(requests) == 3
    with pytest.raises(ValueError, match="already exists"):
        await module.provision(
            settings, path, 3, expected_team_id=99, current_total=50, current_active=41
        )
    assert registrations == 2
    assert database.close.await_count >= 1


async def test_campaign_expiry_while_pacing_prevents_registration(tmp_path, monkeypatch) -> None:
    requests = []
    now = datetime.now(UTC)
    module, settings, _, _ = fake_provision(
        monkeypatch,
        lambda request: requests.append(request) or httpx.Response(500),
        deadline_rows=[(now + timedelta(seconds=1), now), (now, now + timedelta(seconds=1))],
    )
    path = tmp_path / "deadline.json"
    with pytest.raises(RuntimeError, match="private recovery file"):
        await module.provision(
            settings, path, 9, expected_team_id=99, current_total=50, current_active=41
        )
    assert not requests
    saved = json.loads(path.read_text())
    assert all(entry["status"] == "planned" for entry in saved["accounts"])


async def initial_campaign(repo: Repository, accounts: list[str]) -> RedStore:
    repo.settings.accounts = accounts
    now = datetime.now(UTC)
    store = RedStore(repo.db)
    store.scope = "synthetic-replenish-" + uuid4().hex
    store.config = RedSettings(
        _env_file=None,
        expected_team_id=99,
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(hours=47),
        archive_namespace=repo.namespace,
        sessions_json=json.dumps([{"account_id": int(a), "token": "fixture"} for a in accounts]),
    )
    await store.initialize()
    return store


@pytest.mark.integration
async def test_replenishment_retires_only_quarantined_accounts_and_keeps_all_history(repo) -> None:
    store = await initial_campaign(repo, [str(i) for i in range(1, 50)])
    await store.observe([ObservationInput(external_id="1000", text="Synthetic referendum")])
    item = await store.find_observation("1000")
    assert item is not None
    assert await store.plan_reply(item.id, 999, "1", Draft(target_id="1000", text="Fixture"))
    action = await store.claim(["1"])
    assert action is not None
    await store.finish(action, ActionStatus.UNKNOWN, "synthetic_ambiguity", {})
    for identity in range(1, 9):
        await store.quarantine(str(identity), "operator_disabled")
    await store.set_paused(True)
    before = await store.summary()
    deadline = store.config.ends_at
    repo.settings.accounts = [str(i) for i in range(9, 59)]
    await store.amend("Synthetic replacement", verified_accounts={str(i) for i in range(50, 59)})
    await store.initialize()
    assert await store.historical_accounts() == {str(i) for i in range(1, 59)}
    assert len(await store.active_accounts()) == 50
    after = await store.summary()
    assert after["counts"] == before["counts"]
    assert after["action_attempts_last_hour"] == before["action_attempts_last_hour"] == 1
    assert await store.unknown() == [action]
    async with repo.db.sessions() as session:
        campaign = await session.get(RedCampaign, store.scope)
        assert campaign.ends_at == deadline
        revision = await session.scalar(
            select(RedCampaignRevision).where(RedCampaignRevision.scope == store.scope)
        )
        assert set(revision.previous_accounts) == {str(i) for i in range(1, 50)}
        assert set(revision.accounts) == {str(i) for i in range(1, 59)}
    archive = PostArchive(repo.db, repo.namespace)
    await archive.save(
        [
            PostRecord(
                post_id=2000 + identity,
                author_id=identity,
                user_name=None,
                name=None,
                post="Synthetic referendum",
                time_stamp=datetime.now(UTC),
                kind="post",
                npc_status="npc",
            )
            for identity in [1, 50, 999]
        ]
    )
    assert await store.npc_ids() == frozenset({999})
    repo.settings.accounts = [str(i) for i in range(10, 59)]
    with pytest.raises(ValueError):
        await store.initialize()
    repo.settings.accounts = [str(i) for i in range(9, 60)]
    with pytest.raises(ValueError):
        await store.initialize()


@pytest.mark.integration
async def test_new_account_quarantine_does_not_replace_required_identity_verification(repo) -> None:
    store = await initial_campaign(repo, ["10"])
    await store.set_paused(True)
    repo.settings.accounts = ["10", "11"]
    await store.quarantine("11", "operator_disabled")
    with pytest.raises(ValueError):
        await store.amend("Synthetic unverified expansion")
    assert await store.historical_accounts() == {"10"}


def test_executor_historical_own_account_cannot_be_npc_target() -> None:
    transport = SimpleNamespace(settings=SimpleNamespace(accounts=["11"]))
    with pytest.raises(ValueError, match="Own competition accounts"):
        ReplyExecutor(
            transport,
            {"11": RedSession(11, SecretStr("fixture"))},
            npc_author_ids=frozenset({10}),
            own_author_ids=frozenset({10}),
            expected_team_id=99,
            ends_at=datetime.now(UTC) + timedelta(hours=1),
        )


@pytest.mark.integration
async def test_expired_campaign_cannot_append_verified_replacements(repo) -> None:
    store = await initial_campaign(repo, ["10"])
    await store.set_paused(True)
    deadline = datetime.now(UTC) - timedelta(seconds=1)
    store.config = store.config.model_copy(update={"ends_at": deadline})
    async with repo.db.sessions.begin() as session:
        await session.execute(
            update(RedCampaign).where(RedCampaign.scope == store.scope).values(ends_at=deadline)
        )
    repo.settings.accounts = ["10", "11"]
    with pytest.raises(ValueError, match="deadline"):
        await store.amend("Synthetic expired expansion", verified_accounts={"11"})
    assert await store.historical_accounts() == {"10"}
    async with repo.db.sessions() as session:
        assert (
            await session.scalar(
                select(RedCampaignRevision).where(RedCampaignRevision.scope == store.scope)
            )
            is None
        )


async def test_amendment_authenticates_new_identity_even_with_existing_disabled_control(
    monkeypatch,
) -> None:
    import botfluencer.red.operator as module

    settings = provision_settings()
    settings.accounts = ["10", "11"]
    database = SimpleNamespace(close=AsyncMock())
    transport = SimpleNamespace(pacer=SimpleNamespace(scope="synthetic-team"), close=AsyncMock())
    store = SimpleNamespace(
        is_paused=AsyncMock(return_value=True),
        historical_accounts=AsyncMock(return_value={"10"}),
        disabled_accounts=AsyncMock(return_value={"10", "11"}),
        quarantine=AsyncMock(),
        amend=AsyncMock(return_value={"accounts": 2, "max_attempts": 1200}),
    )
    executor = SimpleNamespace(_verified_accounts={"11"}, verify_sessions=AsyncMock())

    def create_executor(*args, **kwargs):
        assert kwargs["disabled_accounts"] == {"10"}
        assert kwargs["own_author_ids"] == frozenset({10})
        return executor

    monkeypatch.setattr(module, "Database", lambda _: database)
    monkeypatch.setattr(module, "TeamPacer", lambda _: transport.pacer)
    monkeypatch.setattr(module, "CompetitionTransport", lambda *_: transport)
    monkeypatch.setattr(module, "RedStore", lambda _: store)
    monkeypatch.setattr(module, "ReplyExecutor", create_executor)
    now = datetime.now(UTC)
    config = SimpleNamespace(
        sessions=lambda _: {},
        expected_team_id=99,
        ends_at=now + timedelta(hours=1),
        enable_replies=True,
        identity_timeout_seconds=30,
    )
    await module.amend(settings, config, "Synthetic verified replacement")
    executor.verify_sessions.assert_awaited_once()
    store.amend.assert_awaited_once_with("Synthetic verified replacement", verified_accounts={"11"})
    database.close.assert_awaited_once()
    transport.close.assert_awaited_once()


@pytest.mark.parametrize("account", [None, {}, {"account_id": True}, {"account_id": "60"}])
async def test_malformed_acknowledgement_retains_issued_token_without_retry(
    tmp_path, monkeypatch, account
) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"account": account, "token": "fixture-issued-token"})

    module, settings, _, _ = fake_provision(monkeypatch, handler)
    path = tmp_path / "malformed-token.json"
    with pytest.raises(RuntimeError, match="private recovery file"):
        await module.provision(
            settings, path, 2, expected_team_id=99, current_total=50, current_active=41
        )
    saved = json.loads(path.read_text())
    assert saved["accounts"][0]["status"] == "unknown"
    assert saved["accounts"][0]["token"] == "fixture-issued-token"
    assert saved["accounts"][1]["status"] == "planned"
    assert len(requests) == 1 and requests[0].method == "POST"


async def test_campaign_expiry_before_identity_read_retains_registration_and_stops(
    tmp_path, monkeypatch
) -> None:
    requests = []
    now = datetime.now(UTC)

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"account": {"account_id": 60}, "token": "fixture-token"})

    live = (now + timedelta(seconds=1), now)
    expired = (now, now + timedelta(seconds=1))
    module, settings, _, _ = fake_provision(
        monkeypatch, handler, deadline_rows=[live, live, expired]
    )
    path = tmp_path / "identity-deadline.json"
    with pytest.raises(RuntimeError, match="private recovery file"):
        await module.provision(
            settings, path, 2, expected_team_id=99, current_total=50, current_active=41
        )
    saved = json.loads(path.read_text())
    assert [entry["status"] for entry in saved["accounts"]] == ["registered", "planned"]
    assert saved["accounts"][0]["account_id"] == 60
    assert saved["accounts"][0]["token"] == "fixture-token"
    assert len(requests) == 1 and requests[0].method == "POST"
