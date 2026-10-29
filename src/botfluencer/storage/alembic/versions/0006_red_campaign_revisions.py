"""Audit explicit account/cap expansions without replacing the campaign history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "red_campaign_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("previous_accounts", postgresql.JSONB(), nullable=False),
        sa.Column("accounts", postgresql.JSONB(), nullable=False),
        sa.Column("previous_max_attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("red_campaign_revisions")
