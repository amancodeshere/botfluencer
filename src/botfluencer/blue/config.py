from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class BlueSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BLUE_", extra="ignore", allow_inf_nan=False)
    namespace: str = Field(default="ctn-2026", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    artifact: Path = Path("/mnt/d/botfluencer/models/blue-active")
    interval_seconds: float = Field(default=30, ge=10, le=3600)
    max_pages: int = Field(default=3, ge=1, le=10)
    score_limit: int = Field(default=128, ge=1, le=1024)
    batch_size: int = Field(default=8, ge=1, le=32)
    max_age_hours: int = Field(default=24, ge=1, le=168)
    min_author_hits: int = Field(default=2, ge=2)
    own_author_ids: list[int] = Field(default_factory=list)
    enable_reports: bool = False
    auto_report: bool = False
    session_token: SecretStr | None = None
    max_pending_reports: int = Field(default=1, ge=1, le=5)
    max_reports_per_hour: int = Field(default=3, ge=1, le=30)
