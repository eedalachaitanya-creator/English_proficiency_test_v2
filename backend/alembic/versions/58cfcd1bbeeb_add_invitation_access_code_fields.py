"""add invitation access code fields

Revision ID: 58cfcd1bbeeb
Revises: 17b107e830d6
Create Date: 2026-05-01 11:00:00.426086

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '58cfcd1bbeeb'
down_revision: Union[str, Sequence[str], None] = '17b107e830d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'invitations',
        sa.Column('access_code', sa.String(length=6), nullable=True),
    )
    op.add_column(
        'invitations',
        sa.Column('failed_code_attempts', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'invitations',
        sa.Column('code_locked', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        "UPDATE invitations "
        "SET access_code = LPAD((FLOOR(RANDOM() * 1000000))::TEXT, 6, '0') "
        "WHERE access_code IS NULL"
    )
    op.alter_column('invitations', 'access_code', nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('invitations', 'code_locked')
    op.drop_column('invitations', 'failed_code_attempts')
    op.drop_column('invitations', 'access_code')
