"""add invitation display_timezone

Revision ID: 8a3f1c2d4e5b
Revises: 9707dcf5aa07
Create Date: 2026-05-04 12:00:00.000000

Adds display_timezone column to the invitations table. This stores the
IANA timezone name (e.g. "Asia/Kolkata", "America/New_York") that the HR
picked when creating the invitation, so the candidate's email can render
the scheduled window in a human-friendly local time.

The database itself stays in UTC — only email rendering uses this column.

Existing rows backfill to "UTC" so the email render code keeps working
for invitations created before this feature.

  display_timezone  String(64), NOT NULL, default 'UTC'
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a3f1c2d4e5b'
down_revision: Union[str, Sequence[str], None] = '9707dcf5aa07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'invitations',
        sa.Column(
            'display_timezone',
            sa.String(length=64),
            nullable=False,
            server_default='UTC',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('invitations', 'display_timezone')