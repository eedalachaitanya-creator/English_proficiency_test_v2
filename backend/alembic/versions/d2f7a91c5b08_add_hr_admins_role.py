"""add hr_admins role column

Revision ID: d2f7a91c5b08
Revises: c14b3e8d72f5
Create Date: 2026-05-04 17:30:00.000000

Adds role column to hr_admins so we can distinguish admins (manage HR
accounts via the new admin portal) from regular HRs (invite candidates,
view results).

Role is one of {'admin', 'hr'} enforced by a CHECK constraint. Existing
rows backfill to 'hr' via the server default — preserving the current
behavior where every account in this table is an HR.

Admin accounts are bootstrapped via `python create_admin.py` on the
server. The new admin portal cannot create admin accounts (deliberate —
admin elevation requires server access).

See docs/superpowers/specs/2026-05-04-admin-portal-design.md.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2f7a91c5b08'
down_revision: Union[str, Sequence[str], None] = 'c14b3e8d72f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'hr_admins',
        sa.Column(
            'role',
            sa.String(length=10),
            nullable=False,
            server_default='hr',
        ),
    )
    op.create_check_constraint(
        'ck_hr_admins_role',
        'hr_admins',
        "role IN ('admin', 'hr')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_hr_admins_role', 'hr_admins', type_='check')
    op.drop_column('hr_admins', 'role')
