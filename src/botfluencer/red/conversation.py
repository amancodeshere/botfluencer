"""Explicit coordinates from fresh verification; metadata is not authority."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class AcceptedParent:
    action_id: UUID
    external_id: str
    account: str


@dataclass(frozen=True)
class Continuation:
    root_id: int
    parent_action_id: UUID
    parent_external_id: str
    npc_author_id: int
