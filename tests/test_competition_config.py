from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from botfluencer.competition.standards import COMPETITION_OPENS, require_open
from botfluencer.config import Settings


def test_team_code_is_private_and_remote_backend_requires_opt_in() -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://test@localhost/botfluencer_test",
        CTN_TEAM_CODE="TESTTEAM00000001",
    )
    assert "TESTTEAM00000001" not in repr(settings)
    assert not settings.ctn_network_enabled
    with pytest.raises(ValidationError, match="read-only observe"):
        Settings(
            _env_file=None,
            DATABASE_URL="postgresql://test@localhost/botfluencer_test",
            platform_backend="ctn",
        )


def test_llm_cap_account_allowance_and_invalid_code_are_rejected() -> None:
    base = {"_env_file": None, "DATABASE_URL": "postgresql://test@localhost/botfluencer_test"}
    with pytest.raises(ValidationError):
        Settings(**base, CTN_LLM_MAX_TOKENS=1025)
    with pytest.raises(ValidationError, match="active allowance"):
        Settings(**base, accounts=["a", "b"], CTN_MAX_ACTIVE_ACCOUNTS=1)
    with pytest.raises(ValidationError, match="16 ASCII"):
        Settings(**base, CTN_TEAM_CODE="not-a-valid-code")


def test_competition_clock_uses_sydney_and_blocks_early_calls() -> None:
    with pytest.raises(RuntimeError, match="19 October"):
        require_open(datetime(2026, 10, 17, tzinfo=UTC))
    require_open(COMPETITION_OPENS)
    assert COMPETITION_OPENS.astimezone(UTC).hour == 6
