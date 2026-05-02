"""add invitation submission reason

Revision ID: 5241fabb4c71
Revises: 59a479c02895
Create Date: 2026-05-01 21:42:10.849355

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5241fabb4c71'
down_revision: Union[str, Sequence[str], None] = '59a479c02895'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'invitations',
        sa.Column('submission_reason', sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('invitations', 'submission_reason')
