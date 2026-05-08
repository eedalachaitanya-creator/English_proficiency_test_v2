"""add teams meeting columns to invitations

Revision ID: 9b4e7f1a2c3d
Revises: 50939905167d
Create Date: 2026-05-08 11:00:00.000000

Adds three columns to the `invitations` table to store the Teams meeting
that is created alongside each invitation:

  - teams_meeting_id   The Graph API meeting object id (used for future
                       lookups, recording retrieval, deletion). Nullable
                       because old invitations created before this feature
                       won't have a meeting attached. New invitations will
                       always have it (the route handler fails the invite
                       if Teams API errors), but historical rows stay
                       valid.

  - teams_join_url     The URL the candidate and HR click to join the
                       Teams meeting. Surfaced in the dashboard, the
                       candidate's email, and the candidate's exam start
                       page. Nullable for the same reason as above.

  - teams_meeting_status  One of: NULL (not attempted, e.g. legacy row),
                          'created' (Teams call succeeded),
                          'failed' (Teams call errored — kept here for
                          dashboards / future retry logic, even though
                          the current behavior fails the invitation
                          entirely on Teams error).

No backfill is needed for existing invitations — leaving teams_* columns
NULL on legacy rows is the correct semantic ("no Teams meeting was
created for this row"). The dashboard and email templates check for
NULL before rendering Teams-specific UI.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9b4e7f1a2c3d"
down_revision = "b7e2c3d8a91f"  # latest head from the EPT project
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invitations",
        sa.Column("teams_meeting_id", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "invitations",
        sa.Column("teams_join_url", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "invitations",
        sa.Column(
            "teams_meeting_status",
            sa.String(length=20),
            nullable=True,
            comment="NULL=not attempted, 'created'=success, 'failed'=Teams API error",
        ),
    )


def downgrade() -> None:
    op.drop_column("invitations", "teams_meeting_status")
    op.drop_column("invitations", "teams_join_url")
    op.drop_column("invitations", "teams_meeting_id")