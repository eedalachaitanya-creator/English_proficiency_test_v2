"""add hr_admins.deleted_at + partial unique index on email

Revision ID: b7e2c3d8a91f
Revises: a0daf6d5675b
Create Date: 2026-05-07 04:30:00.000000

Soft-delete plumbing for HR accounts. The admin portal can mark an
HR row as deleted (set deleted_at = utcnow()) without losing the
candidate-result history — invitations, scores, audio recordings
that the HR sent stay in the DB so audits and reporting still work.

Two pieces:

1. New `deleted_at` column (NULL = active, NOT NULL = soft-deleted).
   Existing rows get NULL via Alembic's default behavior.

2. The unique index on `email` is replaced with a *partial* unique
   index that only enforces uniqueness for active rows
   (`WHERE deleted_at IS NULL`). This lets an admin re-create an
   account with the same email after the original was soft-deleted —
   handy for "I deleted the wrong person, recreate them" or "former HR
   rejoined" cases. Postgres-only feature; we already require Postgres
   so this is fine.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e2c3d8a91f'
down_revision: Union[str, Sequence[str], None] = 'a0daf6d5675b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add deleted_at; swap email index for a partial unique."""
    op.add_column(
        'hr_admins',
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
    )

    # Drop the old plain unique index and replace with a partial unique.
    # Active rows (deleted_at IS NULL) still get email-uniqueness; deleted
    # rows are exempt so their email can be reused.
    op.drop_index('ix_hr_admins_email', table_name='hr_admins')
    op.create_index(
        'ux_hr_admins_email_active',
        'hr_admins',
        ['email'],
        unique=True,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    # Keep a non-unique lookup index on email so equality queries
    # (login, /api/admin/users) stay fast.
    op.create_index('ix_hr_admins_email', 'hr_admins', ['email'], unique=False)


def downgrade() -> None:
    """Restore the plain unique index and drop deleted_at.

    NOTE: downgrade fails if any soft-deleted rows share an email with an
    active row, since the plain unique index can't tolerate that. Clean
    those up manually before downgrading."""
    op.drop_index('ix_hr_admins_email', table_name='hr_admins')
    op.drop_index('ux_hr_admins_email_active', table_name='hr_admins')
    op.create_index('ix_hr_admins_email', 'hr_admins', ['email'], unique=True)
    op.drop_column('hr_admins', 'deleted_at')
