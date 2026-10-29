"""Private opt-ins for the bounded organiser simulation workload."""

import json
from datetime import datetime, timedelta

from pydantic import AwareDatetime, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from botfluencer.config import Settings
from botfluencer.red.executor import RedSession


class RedSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RED_", extra="ignore", env_file=".env")
    enable_replies: bool = False
    expected_team_id: int = Field(gt=0)
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    max_attempts: int = Field(default=1200, ge=1, le=8640)
    archive_namespace: str = Field(default="ctn-2026", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    sessions_json: SecretStr

    @model_validator(mode="after")
    def bounded_window(self) -> "RedSettings":
        duration = self.ends_at - self.starts_at
        if not timedelta(0) < duration <= timedelta(hours=48):
            raise ValueError("Campaign duration must be positive and at most 48 hours")
        return self

    def sessions(self, settings: Settings) -> dict[str, RedSession]:
        data = json.loads(self.sessions_json.get_secret_value())
        if not isinstance(data, list) or not data:
            raise ValueError("Configure private account sessions")
        result = {}
        for item in data:
            account_id = item["account_id"]
            if not isinstance(account_id, int) or isinstance(account_id, bool) or account_id < 1:
                raise ValueError("Invalid session account ID")
            expiry = item.get("expires_at")
            result[str(account_id)] = RedSession(
                account_id=account_id,
                token=SecretStr(item["token"]),
                expires_at=datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                if expiry
                else None,
            )
        if len(result) != len(data) or set(result) != set(settings.accounts):
            raise ValueError("Session IDs must match configured bot accounts")
        if settings.platform_backend != "ctn" or settings.mode != "observe":
            raise ValueError("Red worker requires CTN backend in a dedicated observe namespace")
        if settings.ctn_side != "Vote Yes":
            raise ValueError("This reviewed policy requires the verified Vote Yes assignment")
        if settings.ctn_max_active_accounts is None or settings.ctn_max_total_accounts is None:
            raise ValueError("Verified portal account allowances are required")
        return result
