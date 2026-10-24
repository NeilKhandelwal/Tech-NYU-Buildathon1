"""initial schema: cities, cases, calls, call_cases, transcript, case_events

The first migration is the whole schema. It is not a copy of the old sqlite3 tables:
ids are integers with real foreign keys, cases.notes is gone (notes are case_events
rows with field="note"), and every row carries a city_id.

Revision ID: 0001
Revises:
Create Date: 2026-08-29
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    cities = op.create_table(
        "cities",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("name", sa.String, nullable=False),
    )
    # one deployment, one city for now; every other table defaults its city_id to this row
    op.bulk_insert(cities, [{"id": 1, "name": "Demo City"}])

    op.create_table(
        "cases",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("city_id", sa.Integer, sa.ForeignKey("cities.id"), nullable=False,
                  server_default="1"),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("phone", sa.String, nullable=False),
        sa.Column("issue_type", sa.String),
        sa.Column("description", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="open"),
        sa.Column("lookup_code", sa.String),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
    )
    op.create_index("cases_phone", "cases", ["phone"])
    op.create_index("cases_status", "cases", ["status"])
    op.create_index("cases_lookup_code", "cases", ["lookup_code"])

    op.create_table(
        "calls",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("city_id", sa.Integer, sa.ForeignKey("cities.id"), nullable=False,
                  server_default="1"),
        sa.Column("current_case_id", sa.Integer, sa.ForeignKey("cases.id")),
        sa.Column("status", sa.String, nullable=False, server_default="active"),
        sa.Column("room", sa.String),
        sa.Column("summary", sa.String),
        sa.Column("transfer_reason", sa.String),
        sa.Column("started_at", sa.String, nullable=False),
        sa.Column("ended_at", sa.String),
        sa.Column("updated_at", sa.String, nullable=False),
    )
    op.create_index("calls_status", "calls", ["status"])
    op.create_index("calls_room", "calls", ["room"])

    op.create_table(
        "call_cases",
        sa.Column("call_id", sa.Integer, sa.ForeignKey("calls.id"), primary_key=True),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("cases.id"), primary_key=True),
        sa.Column("how", sa.String, nullable=False),
        sa.Column("linked_at", sa.String, nullable=False),
    )
    op.create_index("call_cases_case", "call_cases", ["case_id"])

    op.create_table(
        "transcript",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("call_id", sa.Integer, sa.ForeignKey("calls.id"), nullable=False),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("text", sa.String, nullable=False),
        sa.Column("ts", sa.String, nullable=False),
    )
    op.create_index("transcript_call", "transcript", ["call_id"])

    op.create_table(
        "case_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("city_id", sa.Integer, sa.ForeignKey("cities.id"), nullable=False,
                  server_default="1"),
        sa.Column("field", sa.String, nullable=False),
        sa.Column("old_value", sa.String),
        sa.Column("new_value", sa.String),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("ts", sa.String, nullable=False),
    )
    op.create_index("case_events_case", "case_events", ["case_id"])


def downgrade() -> None:
    for table in ("case_events", "transcript", "call_cases", "calls", "cases", "cities"):
        op.drop_table(table)
