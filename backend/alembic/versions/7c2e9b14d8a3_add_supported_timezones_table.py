"""add supported_timezones table

Revision ID: 7c2e9b14d8a3
Revises: d2f7a91c5b08
Create Date: 2026-05-04 13:30:00.000000

Creates the supported_timezones table. This replaces the hardcoded
ALLOWED_TIMEZONES allowlist in schemas.py and the _TZ_LABELS dict in
email_service.py with a single editable source of truth.

After this migration ships, adding a new timezone is a SQL statement —
no code change, no deploy, no restart:

    INSERT INTO supported_timezones
        (iana_name, display_label, short_label, sort_order, is_active)
    VALUES
        ('America/Phoenix', 'US Arizona Time (MST)', 'MST', 65, TRUE);

Design notes:
  - is_active is a soft-delete flag. NEVER DELETE rows that have ever
    been used by an invitation. Set is_active=FALSE instead. This
    preserves the row so old invitations referencing its iana_name via
    Invitation.display_timezone can still render emails correctly. The
    relationship is by name only (no FK) — deliberately loose so the
    column accepts legacy values like 'UTC' that aren't in this table.

  - sort_order controls dropdown ordering. Lower values appear first.
    Gaps between values (10, 20, 30...) leave room to insert new zones
    between existing ones without renumbering everything.

  - The seed data below is the same 7 zones the previous hardcoded list
    contained. After this migration runs the system behaves identically
    to before — the only change is that the list now lives in a table
    where it can be edited at runtime.

  - Index on (is_active, sort_order) keeps the dropdown query (the hot
    path: every modal open) at O(1) regardless of how many zones get
    added later. At 7 rows this doesn't matter; at 7000 it would.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c2e9b14d8a3'
down_revision: Union[str, Sequence[str], None] = 'd2f7a91c5b08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'supported_timezones',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('iana_name', sa.String(length=64), nullable=False),
        sa.Column('display_label', sa.String(length=100), nullable=False),
        sa.Column('short_label', sa.String(length=20), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint('iana_name', name='uq_supported_timezones_iana_name'),
    )
    # Composite index on the dropdown query's WHERE + ORDER BY clauses.
    # Postgres can use this as a covering index — the query plan is just
    # an index scan, no table touch needed.
    op.create_index(
        'ix_supported_timezones_active_sort',
        'supported_timezones',
        ['is_active', 'sort_order'],
    )

    # Seed with the 7 zones that were previously hardcoded in schemas.py
    # and email_service.py. Sort order spaced by 10 so future inserts
    # have room to slot between (e.g. Arizona between Pacific and Alaska).
    op.bulk_insert(
        sa.table(
            'supported_timezones',
            sa.column('iana_name', sa.String),
            sa.column('display_label', sa.String),
            sa.column('short_label', sa.String),
            sa.column('sort_order', sa.Integer),
            sa.column('is_active', sa.Boolean),
        ),
        [
            {'iana_name': 'Asia/Kolkata',        'display_label': 'India Standard Time (IST)', 'short_label': 'IST', 'sort_order': 10, 'is_active': True},
            {'iana_name': 'America/New_York',    'display_label': 'US Eastern Time (ET)',       'short_label': 'ET',  'sort_order': 20, 'is_active': True},
            {'iana_name': 'America/Chicago',     'display_label': 'US Central Time (CT)',       'short_label': 'CT',  'sort_order': 30, 'is_active': True},
            {'iana_name': 'America/Denver',      'display_label': 'US Mountain Time (MT)',      'short_label': 'MT',  'sort_order': 40, 'is_active': True},
            {'iana_name': 'America/Los_Angeles', 'display_label': 'US Pacific Time (PT)',       'short_label': 'PT',  'sort_order': 50, 'is_active': True},
            {'iana_name': 'America/Anchorage',   'display_label': 'US Alaska Time (AKT)',       'short_label': 'AKT', 'sort_order': 60, 'is_active': True},
            {'iana_name': 'Pacific/Honolulu',    'display_label': 'US Hawaii Time (HT)',        'short_label': 'HT',  'sort_order': 70, 'is_active': True},
        ],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_supported_timezones_active_sort', table_name='supported_timezones')
    op.drop_table('supported_timezones')