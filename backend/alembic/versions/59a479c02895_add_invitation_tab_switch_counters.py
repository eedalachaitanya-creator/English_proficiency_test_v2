"""add invitation tab switch counters

Revision ID: 59a479c02895
Revises: 58cfcd1bbeeb
Create Date: 2026-05-01 11:00:12.347982

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '59a479c02895'
down_revision: Union[str, Sequence[str], None] = '58cfcd1bbeeb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'invitations',
        sa.Column('tab_switches_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'invitations',
        sa.Column('tab_switches_total_seconds', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('invitations', 'tab_switches_total_seconds')
    op.drop_column('invitations', 'tab_switches_count')
