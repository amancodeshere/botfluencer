"""Persist engagement authority, trend ranks, and explicit mandate renewals."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "engagement_reservations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("account", sa.String(64), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("target_type", sa.String(8), nullable=False),
        sa.Column("action_id", sa.Uuid(), sa.ForeignKey("actions.id"), nullable=False),
        sa.Column("source_action_id", sa.Uuid(), sa.ForeignKey("actions.id"), nullable=True),
        sa.Column("body_hash", sa.String(64), nullable=True),
        sa.Column("root_id", sa.BigInteger(), nullable=True),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("scope", "account", "target_id", "kind"),
        sa.UniqueConstraint("action_id"),
        sa.CheckConstraint("kind IN ('like','repost','team_reply')", name="engagement_kind"),
        sa.CheckConstraint("target_type IN ('own','npc')", name="engagement_target_type"),
    )
    op.create_index(
        "ix_engagement_target_kind", "engagement_reservations", ["scope", "target_id", "kind"]
    )
    op.create_table(
        "trending_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "namespace", sa.String(64), sa.ForeignKey("runtime_controls.namespace"), nullable=False
        ),
        sa.Column("ranked_ids", postgresql.JSONB(), nullable=False),
        sa.Column("ranks", postgresql.JSONB(), nullable=False),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_trending_snapshots_recent", "trending_snapshots", ["namespace", "observed_at"]
    )
    op.create_table(
        "red_mandate_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("previous_starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_max_attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "previous_max_attempts = max_attempts", name="red_mandate_cap_unchanged"
        ),
    )


def downgrade() -> None:
    op.drop_table("red_mandate_revisions")
    op.drop_index("ix_trending_snapshots_recent", table_name="trending_snapshots")
    op.drop_table("trending_snapshots")
    op.drop_index("ix_engagement_target_kind", table_name="engagement_reservations")
    op.drop_table("engagement_reservations")
