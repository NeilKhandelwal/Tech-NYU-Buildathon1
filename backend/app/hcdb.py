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
                        Numeric, String, Table, Text, Uuid, create_engine, insert, inspect,
                        select, update)
from sqlalchemy.exc import NoSuchTableError
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
    # all 311 types, not only heat (supabase/schema.sql adds both to an existing table)
    Column("issue_type", Text),
    Column("description", Text),
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
    global _engine, _missing
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _missing = None


# Columns this code knows that the live table doesn't have yet (schema.sql's later `alter
# table ... add column`). Checked once, on first use: a deploy that lands before someone runs
# the SQL keeps working, reading those fields as None, instead of failing every query mid-call.
_missing: set[str] | None = None


def missing_columns() -> set[str]:
    global _missing
    if _missing is None:
        try:
            present = {c["name"] for c in inspect(engine()).get_columns("cases")}
            _missing = {c.name for c in cases.columns} - present
        except NoSuchTableError:  # tests create the tables after the engine; nothing missing
            _missing = set()
    return _missing


def _case_columns() -> list:
    return [c for c in cases.columns if c.name not in missing_columns()]


def _present(values: dict) -> dict:
    return {k: v for k, v in values.items() if k not in missing_columns()}


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
    out = {k: _plain(v) for k, v in row._mapping.items()}
    # a cases row always has every key, so the API's shape doesn't depend on the migration
    if "case_number" in out:
        out.update({k: None for k in missing_columns() if k not in out})
    return out


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
        conn.execute(insert(cases).values(**_present(values)))
        return _row(conn.execute(select(*_case_columns()).where(cases.c.id == values["id"])).one())


def get_case(case_id) -> dict | None:
    pk = _uuid(case_id)
    if pk is None:
        return None
    with engine().begin() as conn:
        row = conn.execute(select(*_case_columns()).where(cases.c.id == pk)).first()
        return _row(row) if row else None


def get_case_by_number(case_number: str) -> dict | None:
    with engine().begin() as conn:
        row = conn.execute(select(*_case_columns()).where(cases.c.case_number == case_number)).first()
        return _row(row) if row else None


def update_case(case_id, **fields) -> dict:
    pk = _uuid(case_id)
    with engine().begin() as conn:
        conn.execute(update(cases).where(cases.c.id == pk).values(**_present(fields), updated_at=now()))
        return _row(conn.execute(select(*_case_columns()).where(cases.c.id == pk)).one())


def list_cases() -> list[dict]:
    with engine().begin() as conn:
        return [_row(r) for r in conn.execute(select(*_case_columns()).order_by(cases.c.created_at.desc()))]


def add_event(case_id, type: str, actor: str | None, text_original: str | None = None,
              text_en: str | None = None, payload: dict | None = None) -> dict:
    values = {"id": uuid.uuid4(), "case_id": _uuid(case_id), "created_at": now(), "type": type,
              "actor": actor, "text_original": text_original, "text_en": text_en,
              "payload": payload}
    with engine().begin() as conn:
        conn.execute(insert(events).values(**values))
        return _row(conn.execute(select(events).where(events.c.id == values["id"])).one())


def update_event(event_id, **fields) -> None:
    with engine().begin() as conn:
        conn.execute(update(events).where(events.c.id == _uuid(event_id)).values(**fields))


def conversation_events(conversation_id: str, types: tuple[str, ...]) -> list[dict]:
    """Events of these types whose payload names this ElevenLabs conversation, oldest first.
    Filtered in Python, like find_case_by_conversation, so SQLite tests run the same query."""
    with engine().begin() as conn:
        rows = conn.execute(select(events).where(events.c.type.in_(types))
                            .order_by(events.c.created_at))
        return [_row(r) for r in rows if (r.payload or {}).get("conversation_id") == conversation_id]


def link_conversation(conversation_id: str, case_id) -> None:
    """Attach a live call's rows (logged before the agent filed the case) to that case."""
    if not conversation_id:
        return
    loose = [e["id"] for e in conversation_events(conversation_id, ("call_started", "transcript", "call_ended"))
             if e["case_id"] is None]
    if loose:
        with engine().begin() as conn:
            conn.execute(update(events).where(events.c.id.in_([_uuid(i) for i in loose]))
                         .values(case_id=_uuid(case_id)))


def list_events(case_id) -> list[dict]:
    with engine().begin() as conn:
        rows = conn.execute(select(events).where(events.c.case_id == _uuid(case_id))
                            .order_by(events.c.created_at))
        return [_row(r) for r in rows]


def find_case_by_phone(phone: str) -> dict | None:
    """The most recent case for a phone number, stored digits-only."""
    with engine().begin() as conn:
        row = conn.execute(select(*_case_columns()).where(cases.c.resident_phone == digits(phone))
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
                row = conn.execute(select(*_case_columns()).where(cases.c.id == case_id)).first()
                return _row(row) if row else None
    return None
