"""add disabled_at and deleted_at to content tables

Revision ID: 3a8f2c91d4e7
Revises: 50939905167d
Create Date: 2026-05-06 00:00:00.000000

Adds soft-delete + disable toggle support to the four HR-managed content
tables: passages, questions, writing_topics, speaking_topics.

Both columns are nullable timestamps. NULL = active. Non-NULL = the
respective state was set at that moment. Purely additive — no NOT NULL,
no defaults, safe to run on populated tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3a8f2c91d4e7'
down_revision: Union[str, Sequence[str], None] = '50939905167d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CONTENT_TABLES = ('passages', 'questions', 'writing_topics', 'speaking_topics')


def upgrade() -> None:
    for table in CONTENT_TABLES:
        op.add_column(table, sa.Column('disabled_at', sa.DateTime(), nullable=True))
        op.add_column(table, sa.Column('deleted_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    for table in CONTENT_TABLES:
        op.drop_column(table, 'deleted_at')
        op.drop_column(table, 'disabled_at')