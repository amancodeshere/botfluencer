"""Add shadow scoring and a team-deduplicated report ledger; preserve old runtime."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "blue_state",
        sa.Column("namespace", sa.String(64), primary_key=True),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("cursor", sa.Text()),
        sa.Column("anchor", sa.BigInteger()),
        sa.Column("next_anchor", sa.BigInteger()),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("model_version", sa.String(64)),
        sa.Column("last_event", sa.String(64)),
    )
    op.create_table(
        "blue_scores",
        sa.Column("namespace", sa.String(64), primary_key=True),
        sa.Column("post_id", sa.BigInteger(), primary_key=True),
        sa.Column("model_version", sa.String(64), primary_key=True),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("probability >= 0 AND probability <= 1", name="blue_probability"),
    )
    op.create_index(
        "ix_blue_scores_author", "blue_scores", ["namespace", "author_id", "model_version"]
    )
    op.create_table(
        "blue_reports",
        sa.Column("scope", sa.String(64), primary_key=True),
        sa.Column("post_id", sa.BigInteger(), primary_key=True),
        sa.Column("namespace", sa.String(64), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("detail", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="review"),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(32)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("attempted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('review','approved','in_flight','accepted','unknown','rejected')",
            name="blue_report_status",
        ),
    )
    op.create_index("ix_blue_report_dispatch", "blue_reports", ["scope", "status", "created_at"])
    op.create_table(
        "blue_team_controls",
        sa.Column("scope", sa.String(64), primary_key=True),
        sa.Column("blocked_until", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("blue_team_controls")
    op.drop_index("ix_blue_report_dispatch", table_name="blue_reports")
    op.drop_table("blue_reports")
    op.drop_index("ix_blue_scores_author", table_name="blue_scores")
    op.drop_table("blue_scores")
    op.drop_table("blue_state")
