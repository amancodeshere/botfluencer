"""Validated data crossing adapter, strategy, and persistence boundaries."""

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ObservationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=2000)
    source: Literal["fixture", "social", "news"] = "fixture"
    metadata: dict[str, Any] = Field(default_factory=dict)


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["reply", "post", "like", "repost", "team_reply"] = "reply"
    target_id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=500)

    @model_validator(mode="after")
    def validate_text_for_kind(self) -> "Draft":
        if self.kind in {"post", "reply", "team_reply"} and not self.text.strip():
            raise ValueError("Publication text must be nonempty")
        if self.kind in {"like", "repost"} and self.text:
            raise ValueError("Like and repost drafts cannot contain text")
        if self.kind == "team_reply":
            from botfluencer.red.engagement_policy import is_reviewed_team_reply

            if not is_reviewed_team_reply(self.text):
                raise ValueError("Team reply must use a reviewed finite reply")
        return self


class ActionStatus(StrEnum):
    QUEUED = "queued"
    IN_FLIGHT = "in_flight"
    UNKNOWN = "unknown"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ActionIntent(BaseModel):
    id: UUID
    key: str
    account: str
    draft: Draft
    attempts: int


class Receipt(BaseModel):
    external_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class Reconciliation(BaseModel):
    state: Literal["applied", "not_applied", "unresolved"]
    receipt: Receipt | None = None
