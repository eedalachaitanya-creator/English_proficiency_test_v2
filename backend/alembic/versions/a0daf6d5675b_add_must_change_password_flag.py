"""add must_change_password flag

Revision ID: a0daf6d5675b
Revises: 3a8f2c91d4e7
Create Date: 2026-05-06 13:30:04.911281

Adds a boolean flag on hr_admins that is set to TRUE whenever a user
(HR or admin) resets their password via /forgot-password, and cleared
when they call /change-password. Used by:

  - The frontend route guard, which redirects every authenticated
    route to /change-password-required while the flag is true.
  - A new strict auth dependency, which 403s every authenticated
    backend route except an allow-list (/me, /change-password,
    /refresh, /logout) while the flag is true.

Together those make a temp-password emailed to the user unable to
unlock anything beyond the change-password screen, closing the
window where the cleartext temp credential can be abused.

Existing rows get FALSE via the server default — we accept the
small backwards-compat trade-off that any historical user with an
unchanged temp password from before this column existed will not
be retroactively forced. We have no reliable signal to detect
that state.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a0daf6d5675b'
down_revision: Union[str, Sequence[str], None] = '3a8f2c91d4e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'hr_admins',
        sa.Column(
            'must_change_password',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('hr_admins', 'must_change_password')
