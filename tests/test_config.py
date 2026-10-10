import pytest
from pydantic import ValidationError

from botfluencer.config import Settings


def settings(**kwargs: object) -> Settings:
    return Settings(
        _env_file=None, DATABASE_URL="postgresql://test@localhost/botfluencer_test", **kwargs
    )


def test_rejects_live_mode() -> None:
    with pytest.raises(ValidationError, match="Live mode is unavailable"):
        settings(mode="live")


def test_rejects_non_postgres_and_duplicate_accounts() -> None:
    with pytest.raises(ValidationError, match="Only Postgres"):
        Settings(_env_file=None, DATABASE_URL="sqlite:///test.db")
    with pytest.raises(ValidationError, match="distinct"):
        settings(accounts=["same", "same"])


def test_railway_url_normalized_and_password_not_in_repr() -> None:
    config = Settings(_env_file=None, DATABASE_URL="postgres://user:secret@db:5432/app")
    assert config.sqlalchemy_url().drivername == "postgresql+psycopg"
    assert "secret" not in repr(config)


def test_stale_threshold_cannot_be_shorter_than_a_tick() -> None:
    with pytest.raises(ValidationError, match="Heartbeat timeout"):
        settings(heartbeat_timeout_seconds=10)
