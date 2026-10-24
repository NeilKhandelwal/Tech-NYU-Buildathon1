import os
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

# The tables that carry a city_id. call_cases and transcript hang off a call that was
# already fetched under the city scope, so they have no column to filter on.
CITY_TABLES = ("cases", "calls", "case_events")
_READS_CITY_TABLE = re.compile(r'\b(?:from|join)\s+"?(?:%s)"?\b' % "|".join(CITY_TABLES))
_HAS_CITY_CRITERION = re.compile(r"city_id\s*(?:=|in\b)")


class UnscopedRead(AssertionError):
    """A SELECT reached a city-owned table without a city_id criterion."""


@event.listens_for(Engine, "before_cursor_execute")
def forbid_unscoped_reads(conn, cursor, statement, parameters, context, executemany):
    """The city scope, enforced for the whole suite rather than test by test.

    Every SELECT that touches cases, calls or case_events must filter on city_id, so any
    existing test that exercises a read it forgot fails here instead of quietly returning
    another city's rows. A read that is meant to be city-wide says so at the call site with
    `.execution_options(city_scope_exempt=<why>)` — codes.new_code is the one such read.
    """
    if getattr(context, "execution_options", {}).get("city_scope_exempt"):
        return
    sql = statement.lower().lstrip()
    if not sql.startswith("select") or _HAS_CITY_CRITERION.search(sql):
        return
    if _READS_CITY_TABLE.search(sql):
        raise UnscopedRead(f"SELECT with no city_id criterion: {statement}")


def wipe(url: str) -> None:
    """Postgres runs are against one shared database, so each test starts from nothing —
    schema and all, so alembic runs again and the id sequences restart at C-1001 / CALL-1."""
    from sqlalchemy import create_engine

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture
def client(tmp_path, monkeypatch):
    # DATABASE_URL set in the environment (CI's postgres job) runs the same suite there;
    # unset, every test gets its own SQLite file so ids always start at C-1001 / CALL-1
    url = os.environ.get("DATABASE_URL")
    from app import db

    if url:
        wipe(url)
    else:
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    db.reset_engine()
    with TestClient(main_app()) as c:  # TestClient's lifespan runs the migrations
        yield c
    db.reset_engine()


def main_app():
    from app import main

    return main.app
