"""add system_settings table and invitation snapshot columns

Revision ID: 9707dcf5aa07
Revises: 2209d1028651
Create Date: 2026-05-04 01:38:19.012918

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9707dcf5aa07'
down_revision: Union[str, Sequence[str], None] = '2209d1028651'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Two phases:
      1. Create system_settings table + seed the singleton row with
         today's hardcoded defaults.
      2. Add 5 snapshot columns to invitations. NOT NULL with defaults
         matching the historical hardcoded values, so existing rows
         backfill cleanly without changing pre-feature behavior.
    """
    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("max_starts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reading_seconds", sa.Integer(), nullable=False, server_default="1800"),
        sa.Column("writing_seconds", sa.Integer(), nullable=False, server_default="1200"),
        sa.Column("speaking_seconds", sa.Integer(), nullable=False, server_default="600"),
        sa.CheckConstraint("id = 1", name="system_settings_singleton"),
    )
    op.execute(
        "INSERT INTO system_settings (id) VALUES (1) ON CONFLICT DO NOTHING"
    )

    op.add_column(
        "invitations",
        sa.Column("max_starts", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "invitations",
        sa.Column("start_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "invitations",
        sa.Column("reading_seconds", sa.Integer(), nullable=False, server_default="1800"),
    )
    op.add_column(
        "invitations",
        sa.Column("writing_seconds", sa.Integer(), nullable=False, server_default="1200"),
    )
    op.add_column(
        "invitations",
        sa.Column("speaking_seconds", sa.Integer(), nullable=False, server_default="600"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("invitations", "speaking_seconds")
    op.drop_column("invitations", "writing_seconds")
    op.drop_column("invitations", "reading_seconds")
    op.drop_column("invitations", "start_count")
    op.drop_column("invitations", "max_starts")
    op.drop_table("system_settings")
