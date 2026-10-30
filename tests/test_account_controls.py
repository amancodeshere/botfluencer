from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from botfluencer.domain import ActionStatus, Draft, ObservationInput
from botfluencer.red.config import RedSettings
from botfluencer.red.store import RedStore
from botfluencer.storage.repository import Repository


@pytest.mark.integration
async def test_quarantine_preserves_unknown_and_roster_and_survives_restart(
    repo: Repository,
) -> None:
    repo.settings.accounts = ["10", "11"]
    now = datetime.now(UTC)
    store = RedStore(repo.db)
    store.scope = "test-controls-" + uuid4().hex
    store.config = RedSettings(
        _env_file=None,
        expected_team_id=99,
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(hours=47),
        archive_namespace=repo.namespace,
        sessions_json='[{"account_id":10,"token":"fake"},{"account_id":11,"token":"fake2"}]',
    )
    await store.initialize()
    for target, account, author in (("100", "10", 1), ("101", "11", 2), ("102", "10", 3)):
        await store.observe([ObservationInput(external_id=target, text="Synthetic referendum")])
        item = await store.find_observation(target)
        assert item is not None
        assert await store.plan_reply(
            item.id, author, account, Draft(target_id=target, text="Fixture")
        )
    unknown = await store.claim(["10"])
    assert unknown is not None
    await store.finish(unknown, ActionStatus.UNKNOWN, "synthetic_ambiguous", {})
    await store.quarantine("10", "authentication_rejected")
    restarted = RedStore(repo.db)
    restarted.scope = store.scope
    restarted.config = store.config
    await restarted.initialize()
    assert await restarted.active_accounts() == ["11"]
    assert await restarted.next_account() == "11"
    assert await restarted.unknown() == [unknown]
    assert repo.settings.accounts == ["10", "11"]
    assert (await restarted.summary())["action_statuses"] == {
        "failed": 1,
        "queued": 1,
        "unknown": 1,
    }
    claimed = await restarted.claim()
    assert claimed is not None and claimed.account == "11"
    assert await restarted.claim(["10"]) is None
