"""Heat Check data access: Romir's `cases` and `events` tables (CONTRACT.md on
docs/final-context; schema in supabase/schema.sql).

Separate from app/db.py on purpose: that module's Alembic migrations own the old 311 schema,
and they must never run against Supabase, where `cases` is a different table. Here the
schema is created by schema.sql in Supabase, and by create_all only in tests (SQLite).

Every function is sync and returns plain JSON-able dicts: uuid as str, numerics as float,
timestamps as ISO strings.
"""
import os
import random
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (JSON, Boolean, Column, DateTime, ForeignKey, Integer, MetaData,
                        Numeric, String, Table, Text, Uuid, create_engine, insert, select,
                        update)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

metadata = MetaData()
# JSONB on Supabase (the payload->>'conversation_id' index needs it), plain JSON in SQLite tests
Json = JSON().with_variant(JSONB(), "postgresql")

cases = Table(
    "cases", metadata,
    Column("id", Uuid, primary_key=True),
    Column("case_number", Text, unique=True, nullable=False),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
    Column("status", Text, nullable=False),
    Column("resident_name", Text),
    Column("resident_phone", Text, nullable=False),
    Column("language", Text, nullable=False),
    Column("language_label", Text),
    Column("address", Text, nullable=False),
    Column("unit", Text),
    Column("borough", Text),
    Column("lat", Numeric),
    Column("lon", Numeric),
    Column("days_without_heat", Integer),
    Column("indoor_f", Numeric),
    Column("outdoor_f", Numeric),
    Column("is_violation", Boolean),
    Column("rule_applied", Text),
    Column("priority", Text),
    Column("summary_en", Text),
    Column("transcript_original", Text),
    Column("transcript_en", Text),
    Column("building", Json),
    Column("suggested_action", Text),
    Column("caseworker_action", Text),
    Column("caseworker_message_en", Text),
    Column("resident_response", Text),
    Column("resident_response_note_en", Text),
)

events = Table(
    "events", metadata,
    Column("id", Uuid, primary_key=True),
    Column("case_id", Uuid, ForeignKey("cases.id", ondelete="CASCADE")),
    Column("created_at", DateTime(timezone=True)),
    Column("type", String, nullable=False),
    Column("actor", String),
    Column("text_original", Text),
    Column("text_en", Text),
    Column("payload", Json),
)

_engine: Engine | None = None


def database_url() -> str:
    # HC_DATABASE_URL, not DATABASE_URL: the old app reads that one and would migrate it
    url = os.environ.get("HC_DATABASE_URL")
    if not url:
        raise RuntimeError("HC_DATABASE_URL is not set (the Supabase Postgres connection string)")
    # Supabase hands out postgresql://; SQLAlchemy needs the psycopg (v3) driver named
    return re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", url)


def engine() -> Engine:
    global _engine
    if _engine is None:
        # pre_ping: Supabase's pooler drops idle connections, and a demo sits idle between runs
        # the exempt option tells tests/conftest.py's city-scope guard (written for the old 311
        # schema) that this is Romir's `cases`, which has no city_id; it changes no query
        _engine = create_engine(database_url(), pool_pre_ping=True, execution_options={
            "city_scope_exempt": "Heat Check schema has no city_id"})
    return _engine


def reset_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def now() -> datetime:
    return datetime.now(timezone.utc)


def digits(phone: str | None) -> str:
    d = re.sub(r"\D", "", phone or "")
    return d[1:] if len(d) == 11 and d.startswith("1") else d  # +1 E.164 -> ten digits


def new_case_number() -> str:
    # 'HC-' + 6 digits (CONTRACT); the unique index catches the rare clash, see insert_case
    return f"HC-{random.randint(0, 999999):06d}"


def _plain(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row(row) -> dict:
    return {k: _plain(v) for k, v in row._mapping.items()}


def _uuid(value) -> uuid.UUID | None:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None  # a malformed id is just "no such case"


def insert_case(**fields) -> dict:
    ts = now()
    with engine().begin() as conn:
        for _ in range(5):  # 1M numbers; a clash is rare enough that five tries never runs out
            number = new_case_number()
            taken = conn.execute(select(cases.c.id).where(cases.c.case_number == number)).first()
            if taken is None:
                break
        values = {"status": "intake", "priority": "normal", **fields, "id": uuid.uuid4(),
                  "case_number": number, "created_at": ts, "updated_at": ts}
        conn.execute(insert(cases).values(**values))
        return _row(conn.execute(select(cases).where(cases.c.id == values["id"])).one())


def get_case(case_id) -> dict | None:
    pk = _uuid(case_id)
    if pk is None:
        return None
    with engine().begin() as conn:
        row = conn.execute(select(cases).where(cases.c.id == pk)).first()
        return _row(row) if row else None


def get_case_by_number(case_number: str) -> dict | None:
    with engine().begin() as conn:
        row = conn.execute(select(cases).where(cases.c.case_number == case_number)).first()
        return _row(row) if row else None


def update_case(case_id, **fields) -> dict:
    pk = _uuid(case_id)
    with engine().begin() as conn:
        conn.execute(update(cases).where(cases.c.id == pk).values(**fields, updated_at=now()))
        return _row(conn.execute(select(cases).where(cases.c.id == pk)).one())


def list_cases() -> list[dict]:
    with engine().begin() as conn:
        return [_row(r) for r in conn.execute(select(cases).order_by(cases.c.created_at.desc()))]


def add_event(case_id, type: str, actor: str | None, text_original: str | None = None,
              text_en: str | None = None, payload: dict | None = None,
              event_id: uuid.UUID | None = None) -> dict:
    # event_id lets a caller put the event's own id in its payload (an inbound photo's URL)
    values = {"id": event_id or uuid.uuid4(), "case_id": _uuid(case_id), "created_at": now(),
              "type": type, "actor": actor, "text_original": text_original, "text_en": text_en,
              "payload": payload}
    with engine().begin() as conn:
        conn.execute(insert(events).values(**values))
        return _row(conn.execute(select(events).where(events.c.id == values["id"])).one())


def get_event(event_id) -> dict | None:
    pk = _uuid(event_id)
    if pk is None:
        return None
    with engine().begin() as conn:
        row = conn.execute(select(events).where(events.c.id == pk)).first()
        return _row(row) if row else None


def list_events(case_id) -> list[dict]:
    with engine().begin() as conn:
        rows = conn.execute(select(events).where(events.c.case_id == _uuid(case_id))
                            .order_by(events.c.created_at))
        return [_row(r) for r in rows]


def find_case_by_phone(phone: str) -> dict | None:
    """The most recent case for a phone number, stored digits-only."""
    with engine().begin() as conn:
        row = conn.execute(select(cases).where(cases.c.resident_phone == digits(phone))
                           .order_by(cases.c.created_at.desc())).first()
        return _row(row) if row else None


def find_case_by_conversation(conversation_id: str) -> dict | None:
    """The case whose intake_call or callback_call event logged this ElevenLabs conversation."""
    if not conversation_id:
        return None
    with engine().begin() as conn:
        # filtered in Python so the same query runs on SQLite (tests) and Postgres; a demo has
        # a few hundred events at most
        rows = conn.execute(select(events.c.case_id, events.c.payload)
                            .where(events.c.type.in_(("intake_call", "callback_call")))
                            .order_by(events.c.created_at.desc()))
        for case_id, payload in rows:
            if (payload or {}).get("conversation_id") == conversation_id:
                row = conn.execute(select(cases).where(cases.c.id == case_id)).first()
                return _row(row) if row else None
    return None
