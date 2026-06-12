"""add Substack seen entries

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "substack_seen",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=500), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "external_id",
            name="uq_substack_seen_source_entry",
        ),
    )


def downgrade() -> None:
    op.drop_table("substack_seen")
