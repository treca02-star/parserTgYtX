"""Add YouTube source modes and polling state."""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("content_mode", sa.String(20), nullable=False, server_default="all"),
    )
    op.create_table(
        "youtube_seen",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "video_id", name="uq_seen_source_video"),
    )


def downgrade() -> None:
    op.drop_table("youtube_seen")
    op.drop_column("sources", "content_mode")
