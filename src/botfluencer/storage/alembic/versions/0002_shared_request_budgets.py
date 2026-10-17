"""Add durable team-wide pacing without changing the existing action ledger."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "request_budgets",
        sa.Column("scope", sa.String(64), primary_key=True),
        sa.Column("bucket", sa.String(32), primary_key=True),
        sa.Column("interval_seconds", sa.Float(), nullable=False),
        sa.Column(
            "next_allowed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("interval_seconds > 0", name="request_interval_positive"),
    )


def downgrade() -> None:
    op.drop_table("request_budgets")
