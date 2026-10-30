"""Add durable conversation coordinates without changing existing reservations."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("red_targets", sa.Column("conversation_root_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "red_targets", sa.Column("continuation_parent_action_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_red_targets_continuation_parent",
        "red_targets",
        "actions",
        ["continuation_parent_action_id"],
        ["id"],
    )
    op.create_index(
        "ix_red_targets_conversation",
        "red_targets",
        ["scope", "author_id", "conversation_root_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_red_targets_conversation", table_name="red_targets")
    op.drop_constraint("fk_red_targets_continuation_parent", "red_targets", type_="foreignkey")
    op.drop_column("red_targets", "continuation_parent_action_id")
    op.drop_column("red_targets", "conversation_root_id")
