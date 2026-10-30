"""Coverage for a configurable minimum on the shared publication pacer."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select, text, update

from botfluencer.competition.pacing import TeamPacer
from botfluencer.config import Settings
from botfluencer.storage.models import RequestBudget
from botfluencer.storage.repository import Repository


class SessionContext:
    def __init__(self, session: SimpleNamespace) -> None:
        self.session = session

    async def __aenter__(self) -> SimpleNamespace:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeSessions:
    def __init__(self, session: SimpleNamespace) -> None:
        self.session = session

    def begin(self) -> SessionContext:
        return SessionContext(self.session)


def fake_pacer(interval_floor: float) -> tuple[TeamPacer, AsyncMock]:
    session = SimpleNamespace(scalar=AsyncMock(return_value="publish"))
    settings = SimpleNamespace(
        ctn_team_code=SecretStr("PUBLISHFLOOR0001"),
        ctn_publish_min_interval_seconds=interval_floor,
    )
    pacer = TeamPacer(SimpleNamespace(settings=settings, sessions=FakeSessions(session)))
    return pacer, session.scalar


def test_settings_publish_floor_default_and_bounds() -> None:
    default = Settings(_env_file=None, DATABASE_URL="postgresql://test@localhost/test")
    assert default.ctn_publish_min_interval_seconds == 20
    for invalid in (19, 301):
        with pytest.raises(ValidationError):
            Settings(
                _env_file=None,
                DATABASE_URL="postgresql://test@localhost/test",
                CTN_PUBLISH_MIN_INTERVAL_SECONDS=invalid,
            )
    configured = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://test@localhost/test",
        CTN_PUBLISH_MIN_INTERVAL_SECONDS=30,
    )
    assert configured.ctn_publish_min_interval_seconds == 30


async def test_publish_reservation_raises_existing_interval_to_floor() -> None:
    pacer, scalar = fake_pacer(30)
    await pacer.reserve("publish")
    statement, parameters = scalar.await_args.args
    normalized = " ".join(statement.text.split()).lower()
    assert parameters["initial_interval"] == 30
    assert parameters["interval_floor"] == 30
    assert "greatest(request_budgets.interval_seconds, :interval_floor)" in normalized
    assert "where request_budgets.next_allowed_at <= clock_timestamp()" in normalized


async def test_other_bucket_reservation_does_not_receive_publish_floor() -> None:
    pacer, scalar = fake_pacer(30)
    await pacer.reserve("other_read")
    _, parameters = scalar.await_args.args
    assert parameters["bucket"] == "other_read"
    assert parameters["initial_interval"] == 0.2
    assert parameters["interval_floor"] == 0


async def test_publish_headers_cannot_lower_stored_interval_or_backoff() -> None:
    pacer, scalar = fake_pacer(30)
    await pacer.observe("publish", limit=3, reset=60, remaining=2)
    statement, parameters = scalar.await_args.args
    normalized = " ".join(statement.text.split()).lower()
    assert parameters["new_interval"] == 20
    assert parameters["interval_floor"] == 30
    assert "case when :bucket = 'publish' then interval_seconds else 0 end" in normalized
    assert "next_allowed_at = greatest( next_allowed_at, clock_timestamp() +" in normalized
    assert "where scope = :scope and bucket = :bucket" in normalized


@pytest.mark.integration
async def test_pacer_keeps_stricter_publish_backoff_and_leaves_other_buckets(
    repo: Repository,
) -> None:
    repo.db.settings.ctn_team_code = SecretStr("PF" + uuid4().hex[:14].upper())
    repo.db.settings.ctn_publish_min_interval_seconds = 30
    pacer = TeamPacer(repo.db)
    async with repo.db.sessions.begin() as session:
        session.add_all(
            [
                RequestBudget(
                    scope=pacer.scope,
                    bucket="publish",
                    interval_seconds=20,
                    next_allowed_at=datetime.now(UTC) - timedelta(hours=1),
                ),
                RequestBudget(
                    scope=pacer.scope,
                    bucket="other_read",
                    interval_seconds=0.2,
                    next_allowed_at=datetime.now(UTC) + timedelta(seconds=90),
                ),
            ]
        )
    await pacer.reserve("publish")
    async with repo.db.sessions.begin() as session:
        interval_after_reserve = await session.scalar(
            select(RequestBudget.interval_seconds).where(
                RequestBudget.scope == pacer.scope, RequestBudget.bucket == "publish"
            )
        )
        ready_after_reserve = await session.scalar(
            select(
                RequestBudget.next_allowed_at
                >= func.clock_timestamp() + text("INTERVAL '29 seconds'")
            ).where(RequestBudget.scope == pacer.scope, RequestBudget.bucket == "publish")
        )
        assert interval_after_reserve == 30
        assert ready_after_reserve is True
        await session.execute(
            update(RequestBudget)
            .where(RequestBudget.scope == pacer.scope, RequestBudget.bucket == "publish")
            .values(
                interval_seconds=60,
                next_allowed_at=func.clock_timestamp() + text("INTERVAL '120 seconds'"),
            )
        )
        before = await session.scalar(
            select(RequestBudget.next_allowed_at).where(
                RequestBudget.scope == pacer.scope, RequestBudget.bucket == "publish"
            )
        )
        other_before = await session.scalar(
            select(RequestBudget.next_allowed_at).where(
                RequestBudget.scope == pacer.scope, RequestBudget.bucket == "other_read"
            )
        )
    await pacer.observe("publish", limit=3, reset=60, remaining=2)
    async with repo.db.sessions() as session:
        publish = await session.get(RequestBudget, (pacer.scope, "publish"))
        other = await session.get(RequestBudget, (pacer.scope, "other_read"))
        assert publish is not None and other is not None
        assert publish.interval_seconds == 60
        assert publish.next_allowed_at >= before
        assert other.interval_seconds == 0.2
        assert other.next_allowed_at == other_before
