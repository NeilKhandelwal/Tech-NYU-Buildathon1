"""cases.lookup_code is unique

The code is what proves a caller owns a case, so two cases must never share one.
codes.new_code() retries on collision, but that check and the insert are not atomic:
two calls filing at the same moment can both see a code as free. The index is the
backstop the retry loop cannot be.

NULL stays repeatable in both engines — cases filed before codes existed have none.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-30
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("cases_lookup_code", table_name="cases")
    op.create_index("cases_lookup_code", "cases", ["lookup_code"], unique=True)


def downgrade() -> None:
    op.drop_index("cases_lookup_code", table_name="cases")
    op.create_index("cases_lookup_code", "cases", ["lookup_code"])
