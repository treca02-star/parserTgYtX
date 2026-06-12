"""Add deferred content scheduling."""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column(
            "deferred_reminder_time",
            sa.String(5),
            nullable=False,
            server_default="18:00",
        ),
    )
    op.add_column(
        "items",
        sa.Column("deferred_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("items", "deferred_at")
    op.drop_column("settings", "deferred_reminder_time")
