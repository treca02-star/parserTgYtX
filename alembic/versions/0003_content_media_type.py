"""Track downloadable content media types."""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column("media_type", sa.String(20), nullable=False, server_default="none"),
    )
    op.execute("UPDATE items SET media_type='video' WHERE kind='youtube'")


def downgrade() -> None:
    op.drop_column("items", "media_type")
