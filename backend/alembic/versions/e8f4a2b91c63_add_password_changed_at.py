"""add password_changed_at column

Revision ID: e8f4a2b91c63
Revises: d2f7a91c5b08
Create Date: 2026-05-04 18:30:00.000000

Adds password_changed_at to hr_admins so we can invalidate other live
sessions on password rotation. Without this column the change-password
endpoint cannot achieve its core security goal: an attacker who already
has a valid session cookie continues to work after the password change
until the cookie's 8-hour expiry.

After this migration:
  - On login the session stores `pw_v` (the user's current
    password_changed_at value).
  - require_hr / require_admin reject if the session's stored pw_v is
    older than the user's current password_changed_at.
  - The change-password handler bumps password_changed_at AND updates
    the current request's session pw_v so the active tab keeps working.

Existing rows backfill to created_at — they're treated as "password set
at account-creation time", which is conservative and correct (any session
that pre-dates the user's existence can't exist).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8f4a2b91c63'
down_revision: Union[str, Sequence[str], None] = 'd2f7a91c5b08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'hr_admins',
        sa.Column('password_changed_at', sa.DateTime(), nullable=True),
    )
    # Backfill to created_at — treats existing rows as "password set when
    # the account was created", which is the most conservative correct
    # value (no session pre-dates account creation).
    op.execute(
        "UPDATE hr_admins SET password_changed_at = created_at "
        "WHERE password_changed_at IS NULL"
    )
    op.alter_column('hr_admins', 'password_changed_at', nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('hr_admins', 'password_changed_at')
