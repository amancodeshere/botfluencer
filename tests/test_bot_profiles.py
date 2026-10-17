from pathlib import Path

import pytest

from botfluencer.config import Settings
from botfluencer.strategies.baseline import action_key
from botfluencer.strategies.registry import load_profiles


def test_profiles_reject_unknown_strategy_and_account_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "bots.toml"
    path.write_text('[[bots]]\naccount="mock-01"\nstrategy="unreviewed"\n')
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://test@localhost/botfluencer_test",
        bot_profiles_file=path,
    )
    with pytest.raises(ValueError, match="Unregistered"):
        load_profiles(settings)
    path.write_text('[[bots]]\naccount="wrong-account"\nstrategy="noop"\n')
    with pytest.raises(ValueError, match="match BOT_ACCOUNTS"):
        load_profiles(settings)


def test_action_identity_includes_actual_strategy_and_prompt_versions() -> None:
    key = action_key("test", "account", "42", "prompt-v1", "strategy-v1")
    assert key != action_key("test", "account", "42", "prompt-v2", "strategy-v1")
    assert key != action_key("test", "account", "42", "prompt-v1", "strategy-v2")
