"""add invitation valid_from window start

Revision ID: 2209d1028651
Revises: ed0b1ca33840
Create Date: 2026-05-04 00:53:25.978182

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2209d1028651'
down_revision: Union[str, Sequence[str], None] = 'ed0b1ca33840'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Add valid_from as nullable so we can backfill existing rows, then
    enforce NOT NULL. Existing rows are backfilled to created_at so they
    behave as "active immediately on creation" — preserving pre-feature
    behavior where the window opened the moment HR generated the link.
    """
    op.add_column(
        'invitations',
        sa.Column('valid_from', sa.DateTime(), nullable=True),
    )
    op.execute(
        "UPDATE invitations SET valid_from = created_at WHERE valid_from IS NULL"
    )
    op.alter_column('invitations', 'valid_from', nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('invitations', 'valid_from')
