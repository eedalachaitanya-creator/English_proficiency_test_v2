"""add invitation email status

Revision ID: ed0b1ca33840
Revises: 5241fabb4c71
Create Date: 2026-05-02 12:00:00.000000

Adds two columns to the invitations table to track whether the
invitation email actually got sent. Used by the HR dashboard to surface
delivery failures (e.g. "Email failed to send — copy URL manually") so
HR doesn't silently assume the candidate received the email.

  email_status  String(20), NOT NULL, default 'pending'
                Three values: 'pending' | 'sent' | 'failed'

  email_error   String(255), nullable
                Short reason populated only when email_status = 'failed'
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ed0b1ca33840'
down_revision: Union[str, Sequence[str], None] = '5241fabb4c71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'invitations',
        sa.Column(
            'email_status',
            sa.String(length=20),
            nullable=False,
            server_default='pending',
        ),
    )
    op.add_column(
        'invitations',
        sa.Column('email_error', sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('invitations', 'email_error')
    op.drop_column('invitations', 'email_status')