from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
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


class RequestBudget(Base):
    """Team-wide pacing independent of bot account, experiment, and namespace."""

    __tablename__ = "request_budgets"
    __table_args__ = (CheckConstraint("interval_seconds > 0", name="request_interval_positive"),)
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    bucket: Mapped[str] = mapped_column(String(32), primary_key=True)
    interval_seconds: Mapped[float] = mapped_column(Float)
    next_allowed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class NPCPost(Base):
    """Public-post archive; the table name does not prove author NPC identity."""

    __tablename__ = "npc_posts"
    __table_args__ = (
        CheckConstraint(
            "npc_status IN ('unverified','npc','player')", name="npc_post_classification"
        ),
        Index("ix_npc_posts_author_time", "namespace", "author_id", "time_stamp"),
    )
    namespace: Mapped[str] = mapped_column(String(64), primary_key=True)
    post_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    author_id: Mapped[int] = mapped_column(BigInteger)
    user_name: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    post: Mapped[str | None] = mapped_column(Text)
    time_stamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(32))
    npc_status: Mapped[str] = mapped_column(String(16))
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BlueState(Base):
    __tablename__ = "blue_state"
    namespace: Mapped[str] = mapped_column(String(64), primary_key=True)
    paused: Mapped[bool] = mapped_column(default=False, server_default="false")
    cursor: Mapped[str | None] = mapped_column(Text)
    anchor: Mapped[int | None] = mapped_column(BigInteger)
    next_anchor: Mapped[int | None] = mapped_column(BigInteger)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model_version: Mapped[str | None] = mapped_column(String(64))
    last_event: Mapped[str | None] = mapped_column(String(64))


class BlueScore(Base):
    __tablename__ = "blue_scores"
    __table_args__ = (
        CheckConstraint("probability >= 0 AND probability <= 1", name="blue_probability"),
        Index("ix_blue_scores_author", "namespace", "author_id", "model_version"),
    )
    namespace: Mapped[str] = mapped_column(String(64), primary_key=True)
    post_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    model_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    author_id: Mapped[int] = mapped_column(BigInteger)
    probability: Mapped[float] = mapped_column(Float)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BlueReport(Base):
    """One report intent per team/post, independent of model and worker namespace."""

    __tablename__ = "blue_reports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('review','approved','in_flight','accepted','unknown','rejected')",
            name="blue_report_status",
        ),
        Index("ix_blue_report_dispatch", "scope", "status", "created_at"),
    )
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    post_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    namespace: Mapped[str] = mapped_column(String(64))
    author_id: Mapped[int] = mapped_column(BigInteger)
    model_version: Mapped[str] = mapped_column(String(64))
    probability: Mapped[float] = mapped_column(Float)
    detail: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(16), server_default="review")
    correlation_id: Mapped[UUID] = mapped_column(default=uuid4)
    outcome: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BlueTeamControl(Base):
    __tablename__ = "blue_team_controls"
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RedCampaign(Base):
    """One immutable bounded campaign per team; restart cannot extend its mandate."""

    __tablename__ = "red_campaigns"
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(64), unique=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_attempts: Mapped[int]
    archive_namespace: Mapped[str] = mapped_column(String(64))
    accounts: Mapped[list[str]] = mapped_column(JSONB)


class RedTarget(Base):
    """Team/target reservation survives versions, namespaces and ambiguous writes."""

    __tablename__ = "red_targets"
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    post_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    author_id: Mapped[int] = mapped_column(BigInteger)
    action_id: Mapped[UUID] = mapped_column(ForeignKey("actions.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
