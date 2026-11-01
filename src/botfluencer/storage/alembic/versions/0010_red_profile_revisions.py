"""Audit operator profile renames without replacing account identities."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "red_profile_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("account", sa.String(64), nullable=False),
        sa.Column("previous_username", sa.String(100), nullable=False),
        sa.Column("previous_display_name", sa.Text(), nullable=True),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("scope", "account", "previous_username"),
        sa.UniqueConstraint("scope", "username"),
        sa.CheckConstraint(
            "status IN ('planned','in_flight','succeeded','unknown','rejected')",
            name="red_profile_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("red_profile_revisions")
