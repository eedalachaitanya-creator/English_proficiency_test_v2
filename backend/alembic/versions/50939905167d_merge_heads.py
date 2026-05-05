"""merge heads

Revision ID: 50939905167d
Revises: 7c2e9b14d8a3, e8f4a2b91c63
Create Date: 2026-05-05 12:50:32.660687

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '50939905167d'
down_revision: Union[str, Sequence[str], None] = ('7c2e9b14d8a3', 'e8f4a2b91c63')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
