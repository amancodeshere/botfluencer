"""Validated environment settings; no connection strings in diagnostic output."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="BOT_", extra="ignore", allow_inf_nan=False
    )

    database_url: SecretStr = Field(validation_alias="DATABASE_URL")
    namespace: str = Field(default="local-replay", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    mode: Literal["replay", "observe", "live"] = "replay"
    platform_backend: Literal["mock", "ctn"] = "mock"
    strategy: Literal["synthetic", "noop"] = "synthetic"
    bot_profiles_file: Path | None = None
    accounts: list[str] = Field(default_factory=lambda: ["mock-01"], min_length=1)
    poll_interval_seconds: float = Field(default=5, gt=0)
    operation_timeout_seconds: float = Field(default=10, gt=0)
    heartbeat_timeout_seconds: float = Field(default=30, gt=0)
    max_actions_per_hour: int = Field(default=60, ge=0)
    account_cooldown_seconds: float = Field(default=5, ge=0)
    max_llm_calls_per_hour: int = Field(default=60, ge=0)
    max_action_attempts: int = Field(default=3, ge=1)
    retry_base_seconds: float = Field(default=2, gt=0)
    fixture_path: Path = Path("fixtures/feed.jsonl")
    port: int = Field(default=8000, ge=1, le=65535, validation_alias="PORT")
    ctn_team_code: SecretStr | None = Field(default=None, validation_alias="CTN_TEAM_CODE")
    ctn_side: Literal["Vote Yes", "Vote No"] | None = Field(
        default=None, validation_alias="CTN_SIDE"
    )
    ctn_network_enabled: bool = Field(default=False, validation_alias="CTN_NETWORK_ENABLED")
    ctn_max_total_accounts: int | None = Field(
        default=None, ge=1, validation_alias="CTN_MAX_TOTAL_ACCOUNTS"
    )
    ctn_max_active_accounts: int | None = Field(
        default=None, ge=1, validation_alias="CTN_MAX_ACTIVE_ACCOUNTS"
    )
    ctn_feed_limit: int = Field(default=20, ge=1, le=50, validation_alias="CTN_FEED_LIMIT")
    ctn_feed: Literal["trending", "latest"] = Field(default="trending", validation_alias="CTN_FEED")
    ctn_llm_max_tokens: int = Field(
        default=150, ge=1, le=1024, validation_alias="CTN_LLM_MAX_TOKENS"
    )

    @field_validator("ctn_team_code")
    @classmethod
    def team_code_format(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            code = value.get_secret_value().strip()
            if not code:
                return None
            if len(code) != 16 or not code.isascii() or not code.isalnum():
                raise ValueError("Team code must contain 16 ASCII letters/digits")
            return SecretStr(code)
        return value

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except Exception:
            raise ValueError("DATABASE_URL must be a valid Postgres URL") from None
        if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
            raise ValueError("Only Postgres with the psycopg driver is supported")
        if not url.database:
            raise ValueError("DATABASE_URL must name a database")
        return value

    @field_validator("accounts")
    @classmethod
    def distinct_accounts(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(not a.strip() or len(a) > 64 for a in value):
            raise ValueError("Accounts must be distinct, nonempty IDs up to 64 characters")
        return value

    @model_validator(mode="after")
    def runtime_constraints(self) -> "Settings":
        if self.mode == "live":
            raise ValueError("Live mode is unavailable until the official API is integrated")
        if self.platform_backend == "ctn":
            if self.mode != "observe":
                raise ValueError("Competition backend currently supports read-only observe mode")
            if not self.ctn_network_enabled or self.ctn_team_code is None:
                raise ValueError(
                    "Competition access requires explicit network opt-in and team code"
                )
        if self.ctn_max_active_accounts is not None:
            if len(self.accounts) > self.ctn_max_active_accounts:
                raise ValueError("Configured accounts exceed the verified active allowance")
            if (
                self.ctn_max_total_accounts is not None
                and self.ctn_max_active_accounts > self.ctn_max_total_accounts
            ):
                raise ValueError("Active allowance cannot exceed total allowance")
        if self.heartbeat_timeout_seconds <= (
            self.poll_interval_seconds + self.operation_timeout_seconds
        ):
            raise ValueError("Heartbeat timeout must exceed poll interval + operation timeout")
        return self

    def sqlalchemy_url(self) -> URL:
        return make_url(self.database_url.get_secret_value()).set(drivername="postgresql+psycopg")
