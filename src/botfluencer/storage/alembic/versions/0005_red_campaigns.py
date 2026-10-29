"""Add bounded campaign controls and team-wide target reservations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "red_campaigns",
        sa.Column("scope", sa.String(64), primary_key=True),
        sa.Column("namespace", sa.String(64), nullable=False, unique=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("archive_namespace", sa.String(64), nullable=False),
        sa.Column("accounts", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "red_targets",
        sa.Column("scope", sa.String(64), primary_key=True),
        sa.Column("post_id", sa.BigInteger(), primary_key=True),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("action_id", sa.Uuid(), sa.ForeignKey("actions.id"), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("red_targets")
    op.drop_table("red_campaigns")
