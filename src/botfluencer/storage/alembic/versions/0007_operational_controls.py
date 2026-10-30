"""Durable account quarantine, report attempts and collection cycle checkpoints."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "blue_state",
        sa.Column("seen_cursors", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.create_table(
        "blue_report_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("post_id", sa.BigInteger(), nullable=False),
        sa.Column("namespace", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "red_account_controls",
        sa.Column("scope", sa.String(64), primary_key=True),
        sa.Column("account", sa.String(64), primary_key=True),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("reason", sa.String(128), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("red_account_controls")
    op.drop_table("blue_report_attempts")
    op.drop_column("blue_state", "seen_cursors")
