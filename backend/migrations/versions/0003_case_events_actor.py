"""case_events.actor: which person, alongside which system

`source` has always said which system wrote a row ("staff" or "voice"). It cannot say
who: every dashboard write looked identical in the log. `actor` is the signed-in staff
name from the X-Actor header, and stays NULL when there is nobody to name — a voice call,
or a row written before this column existed.

No backfill: the old rows genuinely have no actor, and inventing one would be a lie in
the audit log. NULL renders as nothing.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-30
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("case_events", sa.Column("actor", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("case_events", "actor")
