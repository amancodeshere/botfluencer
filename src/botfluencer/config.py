"""Validated environment settings; no connection strings in diagnostic output."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BOT_", extra="ignore")

    database_url: SecretStr = Field(validation_alias="DATABASE_URL")
    namespace: str = Field(default="local-replay", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    mode: Literal["replay", "observe", "live"] = "replay"
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
        if self.heartbeat_timeout_seconds <= (
            self.poll_interval_seconds + self.operation_timeout_seconds
        ):
            raise ValueError("Heartbeat timeout must exceed poll interval + operation timeout")
        return self

    def sqlalchemy_url(self) -> URL:
        return make_url(self.database_url.get_secret_value()).set(drivername="postgresql+psycopg")
