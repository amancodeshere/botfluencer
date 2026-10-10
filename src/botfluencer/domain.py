"""Validated data crossing adapter, strategy, and persistence boundaries."""

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ObservationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=2000)


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["reply"] = "reply"
    target_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=500)


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
