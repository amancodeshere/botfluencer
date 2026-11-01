"""Profile maintenance preserves identity and does not replay ambiguous writes."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from botfluencer.competition.errors import CompetitionHTTPError, RequestDeferred
from botfluencer.red.config import RedSettings
from botfluencer.red.profiles import ProfileRenamer, fictional_names
from botfluencer.red.store import RedStore
from botfluencer.storage.models import RedProfileRevision
from botfluencer.storage.repository import Repository

NAMES = [
    "Alice Arden",
    "Boris Blake",
    "Celia Clark",
    "Dario Dunn",
    "Elias Evans",
    "Freya Flynn",
    "Gavin Green",
    "Helena Hayes",
    "Isaac Ives",
    "Julia Jones",
    "Kieran Kent",
    "Lydia Lane",
    "Milo Moss",
    "Nadia Nash",
    "Oscar Owen",
]


def test_names_are_unique_recombinations_not_existing_npc_identities() -> None:
    names = fictional_names(NAMES, {"occupied"}, [str(i) for i in range(49)])
    assert len(names) == len({v[0] for v in names.values()}) == 49
    assert len({v[1] for v in names.values()}) == 49
    assert not {v[1].casefold() for v in names.values()} & {v.casefold() for v in NAMES}
    assert all(not handle.startswith("ctnsim_") for handle, _ in names.values())
    assert names == fictional_names(NAMES, {"occupied"}, [str(i) for i in range(49)])
    occupied = {v[0] for v in names.values()}
    changed = fictional_names(NAMES, occupied, [str(i) for i in range(49)])
    assert not occupied & {v[0] for v in changed.values()}


def test_no_fabricated_fallback_when_archive_is_insufficient() -> None:
    with pytest.raises(ValueError, match="Insufficient"):
        fictional_names(["invalid <instruction>"], set(), ["10"])


def mock_renamer() -> tuple[ProfileRenamer, RedProfileRevision]:
    row = RedProfileRevision(
        id=uuid4(),
        scope="synthetic",
        account="10",
        previous_username="ctnsim_test",
        previous_display_name="CTN simulation 1",
        username="alice_blake",
        display_name="Alice Blake",
        reason="synthetic profile maintenance",
        status="planned",
    )
    executor = SimpleNamespace(_verified_accounts={"10"})
    renamer = ProfileRenamer(SimpleNamespace(), executor, SimpleNamespace())
    renamer.read = AsyncMock(
        return_value={
            "username": row.previous_username,
            "display_name": row.previous_display_name,
            "avatar_url": "https://example.invalid/avatar.png",
        }
    )
    renamer._request = AsyncMock(return_value={})

    async def status(revision: RedProfileRevision, value: str) -> None:
        revision.status = value

    renamer.status = AsyncMock(side_effect=status)
    return renamer, row


async def test_rename_uses_reviewed_sdk_fields_and_verifies_readback() -> None:
    renamer, row = mock_renamer()
    renamer.read.side_effect = [
        {
            "username": row.previous_username,
            "display_name": row.previous_display_name,
            "avatar_url": "https://example.invalid/avatar.png",
        },
        {"username": row.username, "display_name": row.display_name},
    ]
    await renamer.apply(row)
    assert row.status == "succeeded"
    renamer._request.assert_awaited_once_with(
        "10",
        body={
            "username": "alice_blake",
            "display_name": "Alice Blake",
            "avatar_url": "https://example.invalid/avatar.png",
        },
    )
    assert renamer.status.await_args_list[0].args[1] == "in_flight"


@pytest.mark.parametrize("state", ["unknown", "in_flight"])
async def test_ambiguous_write_never_reissued_even_when_old_name_remains(state: str) -> None:
    renamer, row = mock_renamer()
    row.status = state
    await renamer.apply(row)
    renamer._request.assert_not_awaited()
    assert row.status == "unknown"
    renamer.read.return_value = {"username": row.username, "display_name": row.display_name}
    await renamer.apply(row)
    assert row.status == "succeeded"
    renamer._request.assert_not_awaited()


@pytest.mark.parametrize(
    "error,expected",
    [
        (httpx.ReadTimeout("synthetic"), "unknown"),
        (CompetitionHTTPError(503), "unknown"),
        (CompetitionHTTPError(409), "rejected"),
        (RequestDeferred(3), "planned"),
    ],
)
async def test_errors_retain_durable_intent(error: Exception, expected: str) -> None:
    renamer, row = mock_renamer()
    renamer._request.side_effect = error
    with pytest.raises(type(error)):
        await renamer.apply(row)
    assert row.status == expected


async def test_concurrent_profile_change_or_unverified_actor_does_not_write() -> None:
    renamer, row = mock_renamer()
    renamer.read.return_value = {"username": "someone_else", "display_name": "Changed"}
    await renamer.apply(row)
    assert row.status == "rejected"
    renamer._request.assert_not_awaited()
    renamer.executor._verified_accounts.clear()
    await renamer.apply(row)
    renamer._request.assert_not_awaited()


@pytest.mark.integration
async def test_profile_audit_survives_restart_and_namespace_ownership(repo: Repository) -> None:
    store = RedStore(repo.db)
    store.scope = "profile-" + uuid4().hex
    now = datetime.now(UTC)
    store.config = RedSettings(
        _env_file=None,
        expected_team_id=99,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=1),
        sessions_json='[{"account_id":10,"token":"synthetic"}]',
    )
    repo.settings.accounts = ["10"]
    await store.initialize()
    executor = SimpleNamespace(_require_enabled=lambda: None, sessions={"10": None})
    async with repo.db.leadership() as connection:
        renamer = ProfileRenamer(store, executor, connection)
        with pytest.raises(ValueError, match="paused"):
            await renamer._guard()
        await store.set_paused(True)
        await renamer._guard()
        async with repo.db.sessions.begin() as session:
            row = RedProfileRevision(
                scope=store.scope,
                account="10",
                previous_username="ctnsim_synthetic",
                username="alice_blake",
                display_name="Alice Blake",
                status="planned",
                reason="synthetic maintenance",
            )
            session.add(row)
        await renamer.status(row, "unknown")
    async with repo.db.sessions() as session:
        saved = await session.scalar(
            select(RedProfileRevision).where(RedProfileRevision.id == row.id)
        )
        assert saved is not None and saved.status == "unknown"
        assert saved.username == "alice_blake"
