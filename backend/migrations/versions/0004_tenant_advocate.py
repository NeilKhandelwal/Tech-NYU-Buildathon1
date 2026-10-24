"""tenant advocate: missions, heat readings, commitments, tenant questions, escalations

The agent now makes calls for a tenant as well as taking them (CONTRACT "## Tenant
advocate"). Every new table arrives in this one migration so the three lanes building on
it never race on revision ids. calls and transcript gain nullable columns only: case calls
and their lines read exactly as before.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "missions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("city_id", sa.Integer, sa.ForeignKey("cities.id"), nullable=False, server_default="1"),
        sa.Column("tenant_name", sa.String, nullable=False),
        sa.Column("tenant_phone", sa.String, nullable=False),
        sa.Column("tenant_language", sa.String, nullable=False, server_default="en"),
        sa.Column("address", sa.String, nullable=False, server_default=""),
        sa.Column("unit", sa.String, nullable=False, server_default=""),
        sa.Column("landlord_name", sa.String, nullable=False, server_default=""),
        sa.Column("landlord_phone", sa.String, nullable=False, server_default=""),
        sa.Column("issue", sa.String),
        sa.Column("goal", sa.String, nullable=False, server_default=""),
        sa.Column("deadline", sa.String),
        sa.Column("limits", sa.String, nullable=False, server_default=""),
        sa.Column("status", sa.String, nullable=False, server_default="draft"),
        sa.Column("outcome", sa.String),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
    )
    op.create_index("missions_status", "missions", ["status"])
    op.create_table(
        "heat_readings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("mission_id", sa.Integer, sa.ForeignKey("missions.id"), nullable=False),
        sa.Column("taken_at", sa.String, nullable=False),
        sa.Column("indoor_f", sa.Float, nullable=False),
        sa.Column("outdoor_f", sa.Float),
        sa.Column("source", sa.String, nullable=False),
    )
    op.create_index("heat_readings_mission", "heat_readings", ["mission_id"])
    op.create_table(
        "commitments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("mission_id", sa.Integer, sa.ForeignKey("missions.id"), nullable=False),
        sa.Column("call_id", sa.Integer, sa.ForeignKey("calls.id")),
        sa.Column("party", sa.String, nullable=False),
        sa.Column("what", sa.String, nullable=False),
        sa.Column("due", sa.String),
        sa.Column("created_at", sa.String, nullable=False),
    )
    op.create_index("commitments_mission", "commitments", ["mission_id"])
    op.create_table(
        "tenant_questions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("mission_id", sa.Integer, sa.ForeignKey("missions.id"), nullable=False),
        sa.Column("call_id", sa.Integer, sa.ForeignKey("calls.id")),
        sa.Column("question", sa.String, nullable=False),
        sa.Column("question_translated", sa.String),
        sa.Column("answer", sa.String),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("answered_at", sa.String),
    )
    op.create_index("tenant_questions_mission", "tenant_questions", ["mission_id"])
    op.create_table(
        "escalations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("mission_id", sa.Integer, sa.ForeignKey("missions.id"), nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("body", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="draft"),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("approved_at", sa.String),
    )
    op.create_index("escalations_mission", "escalations", ["mission_id"])
    # plain ADD COLUMN, not batch mode: a nullable REFERENCES column is legal in SQLite, and a
    # batch rebuild would drop and recreate calls under the rows that point at it
    if op.get_bind().dialect.name == "sqlite":
        # alembic won't emit a constraint in SQLite's ALTER, but SQLite itself accepts this
        op.execute("ALTER TABLE calls ADD COLUMN mission_id INTEGER REFERENCES missions (id)")
    else:
        op.add_column("calls", sa.Column("mission_id", sa.Integer))
        op.create_foreign_key("calls_mission_id_fkey", "calls", "missions", ["mission_id"], ["id"])
    op.add_column("calls", sa.Column("kind", sa.String))
    op.add_column("calls", sa.Column("conversation_id", sa.String))
    op.add_column("transcript", sa.Column("translated", sa.String))


def downgrade() -> None:
    op.drop_column("transcript", "translated")
    for column in ("conversation_id", "kind", "mission_id"):
        op.drop_column("calls", column)
    for table in ("escalations", "tenant_questions", "commitments", "heat_readings", "missions"):
        op.drop_table(table)
