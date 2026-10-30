from pathlib import Path

from pydantic import Field, SecretStr, StrictInt, field_validator, model_validator
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
    own_author_ids: list[StrictInt] = Field(default_factory=list)
    enable_reports: bool = False
    auto_report: bool = False
    session_token: SecretStr | None = None
    reporter_account_id: StrictInt | None = Field(default=None, gt=0)
    expected_team_id: StrictInt | None = Field(default=None, gt=0)
    max_catchup_pages: int = Field(default=10000, ge=10, le=100000)
    max_pending_reports: int = Field(default=1, ge=1, le=5)
    max_reports_per_hour: int = Field(default=3, ge=1, le=30)

    @field_validator("own_author_ids")
    @classmethod
    def distinct_positive_own_ids(cls, value: list[int]) -> list[int]:
        if any(identity < 1 for identity in value) or len(set(value)) != len(value):
            raise ValueError("Own author IDs must be distinct positive integer identifiers")
        return value

    @model_validator(mode="after")
    def report_identity(self) -> "BlueSettings":
        if self.enable_reports and (
            self.reporter_account_id is None
            or self.expected_team_id is None
            or self.reporter_account_id not in self.own_author_ids
            or self.session_token is None
            or not self.session_token.get_secret_value().strip()
        ):
            raise ValueError(
                "Reporting requires a configured own reporter account, team, and token"
            )
        return self
