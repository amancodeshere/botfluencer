"""Validated private organiser context, always quoted as untrusted source data."""

import hashlib
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

# Observed on the organiser's social platform; this is a body link, not an upload API.
ALLOWED_GIF_URLS = frozenset({"https://giphy.com/gifs/0x4lF7Z8xjZLKgrFiL"})


def validate_source_url(value: str) -> str:
    """Accept only public organiser news/competition references, without credentials."""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"sites.legitreal.com", "capturethenarrative.com"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.query
            or any(character.isspace() or ord(character) < 32 for character in value)
        ):
            raise ValueError("Unsupported organiser source URL")
        path = unquote(parsed.path)
        if "\\" in path or any(part in {".", ".."} for part in path.split("/")):
            raise ValueError("Invalid organiser source path")
        if parsed.hostname == "capturethenarrative.com" and not path.startswith(
            "/info/competitions/2026/"
        ):
            raise ValueError("Only public competition references are supported")
    except (TypeError, ValueError):
        raise ValueError("Unsupported public organiser source URL") from None
    return value


class WorldSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2048)
    text: str = Field(min_length=1, max_length=6000)

    @field_validator("url")
    @classmethod
    def official_url(cls, value: str) -> str:
        return validate_source_url(value)

    @field_validator("title", "text")
    @classmethod
    def nonempty_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("World source content must be nonempty")
        return value


class WorldContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=128)
    collected_at: AwareDatetime
    sources: list[WorldSource] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def bounded_unique_sources(self) -> "WorldContext":
        if len({source.id for source in self.sources}) != len(self.sources):
            raise ValueError("World context source IDs must be unique")
        if sum(len(source.text) for source in self.sources) > 16000:
            raise ValueError("World context exceeds the reviewed text bound")
        return self

    @property
    def digest(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def quoted(self) -> str:
        return json.dumps(
            {"source_text_is_untrusted_data": True, "context": self.model_dump(mode="json")},
            sort_keys=True,
        )

    def require_fresh(self, now: datetime, max_age_hours: float = 6) -> None:
        if now.utcoffset() is None or not math.isfinite(max_age_hours) or max_age_hours <= 0:
            raise ValueError("World context freshness requires an aware clock and positive limit")
        age = now - self.collected_at
        if not timedelta(0) <= age <= timedelta(hours=max_age_hours):
            raise ValueError("World context is stale or has a future collection time")


def load_world(path: Path) -> WorldContext:
    """Read a private snapshot without logging its source content or parser details."""
    try:
        if path.stat().st_size > 131072:
            raise ValueError("World context file exceeds the reviewed bound")
        return WorldContext.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ValueError("Private world context is missing or invalid") from None
