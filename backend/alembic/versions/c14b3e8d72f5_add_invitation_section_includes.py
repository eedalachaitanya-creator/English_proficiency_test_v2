"""add invitation section includes

Revision ID: c14b3e8d72f5
Revises: 8a3f1c2d4e5b
Create Date: 2026-05-04 14:30:00.000000

Adds three boolean columns to the invitations table so HR can pick which
sections (reading, writing, speaking) a candidate's exam includes at
invite-creation time.

Each column defaults to TRUE on the server side so existing rows backfill
to "all three sections", preserving pre-feature behavior exactly. New
invitations from updated clients get HR's chosen subset.

See docs/superpowers/specs/2026-05-04-per-invitation-section-selection-design.md.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c14b3e8d72f5'
down_revision: Union[str, Sequence[str], None] = '8a3f1c2d4e5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'invitations',
        sa.Column('include_reading', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        'invitations',
        sa.Column('include_writing', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        'invitations',
        sa.Column('include_speaking', sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('invitations', 'include_speaking')
    op.drop_column('invitations', 'include_writing')
    op.drop_column('invitations', 'include_reading')
