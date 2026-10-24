"""sms: every text sent to or received from a tenant (CONTRACT "## SMS")

A new table only; nothing existing changes shape.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sms",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("city_id", sa.Integer, sa.ForeignKey("cities.id"), nullable=False, server_default="1"),
        sa.Column("mission_id", sa.Integer, sa.ForeignKey("missions.id")),
        sa.Column("direction", sa.String, nullable=False),
        sa.Column("phone", sa.String, nullable=False),
        sa.Column("body", sa.String, nullable=False),
        sa.Column("body_en", sa.String),
        sa.Column("language", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("error", sa.String),
        sa.Column("twilio_sid", sa.String),
        sa.Column("created_at", sa.String, nullable=False),
    )
    op.create_index("sms_mission", "sms", ["mission_id"])
    op.create_index("sms_phone", "sms", ["phone"])


def downgrade() -> None:
    op.drop_table("sms")
