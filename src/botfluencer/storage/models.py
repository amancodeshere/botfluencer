from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Control(Base):
    __tablename__ = "runtime_controls"
    namespace: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16))
    paused: Mapped[bool] = mapped_column(default=False, server_default="false")
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[UUID | None]


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (UniqueConstraint("namespace", "external_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace: Mapped[str] = mapped_column(ForeignKey("runtime_controls.namespace"))
    external_id: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (UniqueConstraint("namespace", "key"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace: Mapped[str] = mapped_column(ForeignKey("runtime_controls.namespace"))
    key: Mapped[str] = mapped_column(String(64))
    observation_id: Mapped[UUID] = mapped_column(ForeignKey("observations.id"))
    account: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(64))
    rationale: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Action(Base):
    __tablename__ = "actions"
    __table_args__ = (
        UniqueConstraint("namespace", "key"),
        UniqueConstraint("decision_id"),
        CheckConstraint(
            "status IN ('queued','in_flight','unknown','succeeded','failed')", name="action_status"
        ),
        CheckConstraint("attempts >= 0", name="action_attempts"),
        Index("ix_actions_dispatch", "namespace", "status", "next_attempt_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace: Mapped[str] = mapped_column(ForeignKey("runtime_controls.namespace"))
    decision_id: Mapped[UUID] = mapped_column(ForeignKey("decisions.id"))
    key: Mapped[str] = mapped_column(String(64))
    account: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Outcome(Base):
    __tablename__ = "outcomes"
    __table_args__ = (Index("ix_outcomes_action", "action_id", "created_at"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    action_id: Mapped[UUID] = mapped_column(ForeignKey("actions.id"))
    attempt: Mapped[int]
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_budget", "namespace", "kind", "created_at"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace: Mapped[str] = mapped_column(ForeignKey("runtime_controls.namespace"))
    kind: Mapped[str] = mapped_column(String(32))
    account: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MockReceipt(Base):
    """Synthetic remote-side state, separate from the worker's action ledger."""

    __tablename__ = "mock_receipts"
    __table_args__ = (UniqueConstraint("namespace", "key"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace: Mapped[str] = mapped_column(ForeignKey("runtime_controls.namespace"))
    key: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
