import tomllib
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from botfluencer.config import Settings
from botfluencer.strategies.base import Strategy
from botfluencer.strategies.baseline import NoopStrategy, SyntheticStrategy

STRATEGIES: dict[str, Callable[[], Strategy]] = {
    "synthetic": SyntheticStrategy,
    "noop": NoopStrategy,
}


class BotProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account: str = Field(min_length=1, max_length=64)
    role: str = Field(default="baseline", max_length=64)
    strategy: str = "noop"
    enabled: bool = True


class ProfileDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bots: list[BotProfile]


def load_profiles(settings: Settings) -> dict[str, BotProfile]:
    if settings.bot_profiles_file is None:
        profiles = [
            BotProfile(account=name, strategy=settings.strategy) for name in settings.accounts
        ]
    else:
        with settings.bot_profiles_file.open("rb") as stream:
            profiles = ProfileDocument.model_validate(tomllib.load(stream)).bots
    result = {profile.account: profile for profile in profiles}
    if len(result) != len(profiles) or set(result) != set(settings.accounts):
        raise ValueError("Bot profile IDs must be unique and match BOT_ACCOUNTS")
    if any(profile.strategy not in STRATEGIES for profile in profiles):
        raise ValueError("Unregistered bot strategy; add a reviewed implementation to the registry")
    return result
