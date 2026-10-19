"""Add a deduplicated read-only public-post archive."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "npc_posts",
        sa.Column("namespace", sa.String(64), primary_key=True),
        sa.Column("post_id", sa.BigInteger(), primary_key=True),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("user_name", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("post", sa.Text(), nullable=True),
        sa.Column("time_stamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("npc_status", sa.String(16), nullable=False),
        sa.Column(
            "collected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "npc_status IN ('unverified','npc','player')", name="npc_post_classification"
        ),
    )
    op.create_index(
        "ix_npc_posts_author_time", "npc_posts", ["namespace", "author_id", "time_stamp"]
    )


def downgrade() -> None:
    op.drop_index("ix_npc_posts_author_time", table_name="npc_posts")
    op.drop_table("npc_posts")
