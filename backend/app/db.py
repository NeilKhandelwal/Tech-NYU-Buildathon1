"""The data layer: SQLAlchemy Core tables, one engine, and the public-id helpers.

Core, not the ORM: every query stays a visible statement at the call site in main.py.
Alembic owns the schema — nothing in this module creates or alters a table.

Timestamps are TEXT, the same "2026-08-28T20:01:02Z" strings the API has always returned.
They sort lexicographically, which is all the `since` cursor needs, and they mean SQLite
and Postgres store and compare them identically.
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import (Column, ForeignKey, Index, Integer, MetaData, Row, String, Table,
                        create_engine, event, inspect)
from sqlalchemy.engine import Engine

BACKEND_DIR = Path(__file__).resolve().parent.parent

# by path, not by cwd: the app, the alembic CLI and the scripts all have to read the same
# DATABASE_URL whichever directory they were started from. Never overrides a real env var.
load_dotenv(BACKEND_DIR / ".env")


def database_url() -> str:
    """DATABASE_URL wins; CASES_DB is the old name and only ever meant a SQLite file."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    legacy = os.environ.get("CASES_DB")
    if legacy:
        print("CASES_DB is deprecated: set DATABASE_URL=sqlite:///<path> instead")
        return "sqlite:///" + legacy
    return "sqlite:///" + str(BACKEND_DIR / "cases.db")


# the city row migration 0001 seeds; one deployment serves one city until Phase 3 makes
# cities real, so the scope is deployment-wide rather than per-request.
DEFAULT_CITY_ID = 1


def current_city_id() -> int:
    """The city this deployment serves — every read filters on it and every insert writes it.

    Read here and nowhere else: a per-request city later (a header, a staff account) is a
    change to this one function rather than to every endpoint.
    """
    return int(os.environ.get("CITY_ID") or DEFAULT_CITY_ID)


metadata = MetaData()

# One row, id 1, until Phase 3 makes cities real. city_id is written on every row now so
# adding the scope later is a query change, not a migration of live data.
cities = Table(
    "cities", metadata,
    Column("id", Integer, primary_key=True, autoincrement=False),
    Column("name", String, nullable=False),
)

cases = Table(
    "cases", metadata,
    Column("id", Integer, primary_key=True),
    Column("city_id", Integer, ForeignKey("cities.id"), nullable=False, server_default="1"),
    Column("name", String, nullable=False),
    Column("phone", String, nullable=False),
    Column("issue_type", String),  # NULL = not classified yet (the agent fills it in mid-call)
    Column("description", String, nullable=False),
    Column("status", String, nullable=False, server_default="open"),
    Column("lookup_code", String),  # NULL = filed before codes existed; can't be looked up by voice
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Index("cases_phone", "phone"),
    Index("cases_status", "status"),
    # unique: the code is what proves a caller owns the case (migration 0002)
    Index("cases_lookup_code", "lookup_code", unique=True),
)

calls = Table(
    "calls", metadata,
    Column("id", Integer, primary_key=True),
    Column("city_id", Integer, ForeignKey("cities.id"), nullable=False, server_default="1"),
    # the case the agent is working right now; every link lives in call_cases
    Column("current_case_id", Integer, ForeignKey("cases.id")),
    Column("status", String, nullable=False, server_default="active"),
    Column("room", String),
    Column("summary", String),
    Column("transfer_reason", String),
    Column("started_at", String, nullable=False),
    Column("ended_at", String),
    Column("updated_at", String, nullable=False),  # what GET /calls?since= compares against
    Index("calls_status", "status"),
    Index("calls_room", "room"),
)

# a call can create or look up several cases; this is the truth about which,
# while calls.current_case_id is only the cursor (the case the agent is working right now)
call_cases = Table(
    "call_cases", metadata,
    Column("call_id", Integer, ForeignKey("calls.id"), primary_key=True),
    Column("case_id", Integer, ForeignKey("cases.id"), primary_key=True),
    Column("how", String, nullable=False),  # 'created' | 'looked_up'
    Column("linked_at", String, nullable=False),
    Index("call_cases_case", "case_id"),
)

transcript = Table(
    "transcript", metadata,
    Column("id", Integer, primary_key=True),
    Column("call_id", Integer, ForeignKey("calls.id"), nullable=False),
    Column("role", String, nullable=False),
    Column("text", String, nullable=False),
    Column("ts", String, nullable=False),
    Index("transcript_call", "call_id"),
)

# field="note" rows are the notes themselves: a note is an event with a source and a time
case_events = Table(
    "case_events", metadata,
    Column("id", Integer, primary_key=True),
    Column("case_id", Integer, ForeignKey("cases.id"), nullable=False),
    Column("city_id", Integer, ForeignKey("cities.id"), nullable=False, server_default="1"),
    Column("field", String, nullable=False),
    Column("old_value", String),
    Column("new_value", String),
    Column("source", String, nullable=False),
    Column("actor", String),  # NULL = nobody named: a voice call, or a write from before 0003
    Column("ts", String, nullable=False),
    Index("case_events_case", "case_id"),
)

_engine: Engine | None = None


def _sqlite_foreign_keys(dbapi_conn, _record) -> None:
    # SQLite enforces foreign keys only when asked, and only for the connection that asks
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(database_url())
        if _engine.dialect.name == "sqlite":
            event.listen(_engine, "connect", _sqlite_foreign_keys)
    return _engine


def reset_engine() -> None:
    """Forget the cached engine so the next call re-reads DATABASE_URL (tests, scripts)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def connect():
    """A transaction that commits on a clean exit — `with db.connect() as conn:` everywhere."""
    return engine().begin()


# tables the pre-migration sqlite3 layer created; a file holding these and no alembic_version
# was written before this schema existed
LEGACY_TABLES = {"cases", "calls", "transcript", "case_events"}


def refuse_legacy_database() -> None:
    """Stop before the first CREATE TABLE if this database predates migrations.

    Upgrading such a file would run the initial migration against tables that already exist.
    SQLite DDL is not transactional, so the failure lands halfway: some new tables created,
    the rest not, and the next boot fails on a different table. There is no in-place upgrade
    from that schema -- the ids and the notes column both changed shape -- so refuse loudly
    while the file is still intact.
    """
    tables = set(inspect(engine()).get_table_names())
    if "alembic_version" in tables or not tables & LEGACY_TABLES:
        return  # already versioned, or empty enough that there is nothing to collide with
    raise RuntimeError(
        f"{database_url()} predates migrations: it has "
        f"{sorted(tables & LEGACY_TABLES)} but no alembic_version table, so there is nothing "
        "to upgrade from. Move the database aside (or delete it) and let this start empty -- "
        "`uv run python -m scripts.reset_demo` then rebuilds it with the sample cases."
    )


def init_db() -> None:
    """Bring the database up to head. The only schema path there is; app startup calls it."""
    from alembic import command
    from alembic.config import Config

    refuse_legacy_database()  # before any DDL: a half-migrated file is worse than a refusal
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(cfg, "head")


# Public ids are derived here and nowhere else; the database only ever stores the integer.
# The id columns are 4-byte integers, which is what bounds a parsed id: a bigger number is
# not a row that is missing, it is a value the driver refuses to bind (Postgres raises
# NumericValueOutOfRange, SQLite overflows), and that would be a 500 on a typo.
MAX_PK = 2**31 - 1


def _parse_pk(public_id: str, prefix: str, offset: int) -> int | None:
    """A public id back to its row id, or None for anything that cannot be one, so every
    endpoint answers 404 rather than 500."""
    try:
        pk = int(public_id.removeprefix(prefix)) - offset
    except ValueError:
        return None
    return pk if 0 <= pk <= MAX_PK else None


def case_id(pk: int) -> str:
    return f"C-{1000 + pk}"


def case_pk(case_id: str) -> int | None:
    return _parse_pk(case_id, "C-", 1000)  # "C-1001" -> 1


def call_id(pk: int) -> str:
    return f"CALL-{pk}"


def call_pk(call_id: str) -> int | None:
    return _parse_pk(call_id, "CALL-", 0)


def row_to_case(row: Row) -> dict:
    d = dict(row._mapping)
    d["id"] = case_id(d.pop("id"))
    return d


def row_to_call(row: Row) -> dict:
    d = dict(row._mapping)
    d["id"] = call_id(d.pop("id"))
    # calls.current_case_id is the JSON's case_id: the cursor, in public form
    pk = d.pop("current_case_id")
    d["case_id"] = case_id(pk) if pk is not None else None
    return d
