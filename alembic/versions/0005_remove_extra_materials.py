"""Remove unreliable linked-material counts."""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("items", "extra_materials")


def downgrade() -> None:
    op.add_column(
        "items",
        sa.Column("extra_materials", sa.Integer(), nullable=False, server_default="0"),
    )
