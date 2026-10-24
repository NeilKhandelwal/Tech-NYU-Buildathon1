"""What the data layer itself has to guarantee: migrations, foreign keys, ids, ?since=."""
import os
import sqlite3

import pytest
from sqlalchemy import inspect, insert, select
from sqlalchemy.exc import IntegrityError

from app import db
from tests.conftest import wipe
from tests.test_cases import BODY


# the shape backend/app/db.py wrote before this branch: our table names, no alembic_version
LEGACY_SCHEMA = """
CREATE TABLE cases (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT NOT NULL,
    issue_type TEXT, description TEXT NOT NULL, lookup_code TEXT,
    status TEXT NOT NULL DEFAULT 'open', notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE calls (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT,
    status TEXT NOT NULL DEFAULT 'active', started_at TEXT NOT NULL, ended_at TEXT,
    room TEXT, summary TEXT, transfer_reason TEXT);
CREATE TABLE call_cases (
    call_id TEXT NOT NULL, case_id TEXT NOT NULL, how TEXT NOT NULL, linked_at TEXT NOT NULL,
    PRIMARY KEY (call_id, case_id));
CREATE TABLE transcript (
    id INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT NOT NULL, role TEXT NOT NULL,
    text TEXT NOT NULL, ts TEXT NOT NULL);
CREATE TABLE case_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, field TEXT NOT NULL,
    old_value TEXT, new_value TEXT, source TEXT NOT NULL, ts TEXT NOT NULL);
"""


def test_a_database_from_before_migrations_is_refused_with_the_file_intact(tmp_path, monkeypatch):
    """A deployed cases.db still has the old shape. Upgrading it runs CREATE TABLE over tables
    that already exist, and SQLite DDL is not transactional: the boot dies partway through with
    some new tables created, and the next boot dies on a different one. There is no in-place
    upgrade — the ids and the notes column both changed shape — so the only safe answer is to
    refuse before the first statement and say what to do."""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    db.reset_engine()
    try:
        with pytest.raises(RuntimeError, match="predates migrations"):
            db.init_db()
        conn = sqlite3.connect(path)
        after = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        # nothing was written: no half-created tables for the next boot to trip over
        assert "cities" not in after and "alembic_version" not in after
        db.reset_engine()
        with pytest.raises(RuntimeError, match="predates migrations"):
            db.init_db()  # and the refusal is stable, not a different error each time
    finally:
        db.reset_engine()


def test_an_empty_database_migrates_and_a_migrated_one_is_left_alone(tmp_path, monkeypatch):
    """The guard only ever catches the legacy shape. A clean clone has no file at all, and
    every boot after the first runs against a database this code already migrated."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'clean.db'}")
    db.reset_engine()
    try:
        db.init_db()  # empty: nothing to collide with
        db.init_db()  # versioned: alembic_version says head, so the guard stands aside
        assert "cases" in set(inspect(db.engine()).get_table_names())
    finally:
        db.reset_engine()


def test_migration_builds_the_whole_schema_from_an_empty_database(tmp_path, monkeypatch):
    """A clean clone has no cases.db at all, so `alembic upgrade head` is the only thing
    standing between a fresh checkout and a working desk. Running it twice is a boot."""
    url = os.environ.get("DATABASE_URL")
    if url:
        wipe(url)  # the postgres run tests the same thing on the engine compose and CI use
    else:
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fresh.db'}")
    db.reset_engine()
    try:
        db.init_db()
        tables = set(inspect(db.engine()).get_table_names())
        assert set(db.metadata.tables) <= tables
        assert "alembic_version" in tables  # the schema's version is recorded, not guessed
        with db.connect() as conn:
            # seeded by the migration: every other table defaults its city_id to this row
            assert [tuple(r) for r in conn.execute(select(db.cities))] == [(1, "Demo City")]
        db.init_db()  # app startup runs it every boot; the second pass must change nothing
    finally:
        db.reset_engine()


@pytest.mark.parametrize("pk", [1, 7, 999, 1234])
def test_public_ids_round_trip(pk):
    """The database stores integers; C-1001 and CALL-7 exist only in the API layer. Every
    endpoint takes the public form, so the two directions have to agree exactly."""
    assert db.case_pk(db.case_id(pk)) == pk
    assert db.call_pk(db.call_id(pk)) == pk
    assert db.case_id(1) == "C-1001" and db.call_id(7) == "CALL-7"


@pytest.mark.parametrize("bad", ["garbage", "C-", "CALL-x", "", "C-nine",
                                 "C-99999999999999999999999", "CALL-99999999999999999999999",
                                 "C-0", "CALL--5"])
def test_malformed_public_ids_are_none_so_endpoints_404(bad):
    """A caller reading a mangled id back to the agent must get "no such case", not a 500.
    Out-of-range is malformed too: the id columns are 4-byte integers, so a number past that
    is not a missing row, it is a bind the driver refuses — a 500 on somebody's typo."""
    assert db.case_pk(bad) is None
    assert db.call_pk(bad) is None


def test_out_of_range_public_ids_404_on_every_endpoint_that_takes_one(client):
    """The path the reviewer walked: a huge id reached the driver and came back a 500."""
    huge_case, huge_call = "C-99999999999999999999999", "CALL-99999999999999999999999"
    client.post("/calls")
    assert client.get(f"/cases/{huge_case}").status_code == 404
    assert client.get(f"/cases/{huge_case}/events").status_code == 404
    assert client.get(f"/cases/{huge_case}/calls").status_code == 404
    assert client.patch(f"/cases/{huge_case}", json={"status": "resolved"}).status_code == 404
    assert client.post(f"/cases/{huge_case}/notes", json={"text": "x"}).status_code == 404
    assert client.get(f"/calls/{huge_call}").status_code == 404
    assert client.patch(f"/calls/{huge_call}", json={"status": "ended"}).status_code == 404
    assert client.post(f"/calls/{huge_call}/transcript", json={"role": "user", "text": "x"}).status_code == 404
    assert client.patch("/calls/CALL-1", json={"case_id": huge_case}).status_code == 404
    assert client.post("/calls/CALL-1/cases", json={"case_id": huge_case}).status_code == 404


def test_database_rejects_a_link_to_a_case_that_does_not_exist(client):
    """The point of row-id foreign keys: the constraint lives in the database, so a bug that
    slips past main.py's check still cannot orphan a call on a case page."""
    client.post("/calls")
    with pytest.raises(IntegrityError):
        with db.connect() as conn:
            conn.execute(insert(db.call_cases).values(
                call_id=1, case_id=999, how="created", linked_at="2030-01-01T00:00:00Z"))


def test_database_rejects_a_transcript_line_for_a_call_that_does_not_exist(client):
    """Same rule one table over: a transcript with no call is a line nothing can ever show."""
    with pytest.raises(IntegrityError):
        with db.connect() as conn:
            conn.execute(insert(db.transcript).values(
                call_id=999, role="user", text="hi", ts="2030-01-01T00:00:00Z"))


def test_the_api_still_answers_404_not_500_on_those_paths(client):
    """The agent speaks every backend failure aloud, so the constraint must never be what
    the caller hears: main.py checks first and answers 404 in a shape the tools handle."""
    client.post("/calls")
    client.post("/cases", json=BODY)
    assert client.post("/calls/CALL-1/cases", json={"case_id": "C-9999"}).status_code == 404
    assert client.patch("/calls/CALL-1", json={"case_id": "C-9999"}).status_code == 404
    assert client.post("/calls/CALL-9/transcript", json={"role": "user", "text": "x"}).status_code == 404
    assert client.post("/calls/CALL-9/cases", json={"case_id": "C-1001"}).status_code == 404


def test_cases_since_returns_only_what_changed(client, monkeypatch):
    """A dashboard back from a long disconnect refetches everything it missed. With ?since=
    that costs one small response; without it, the whole table on every reconnect."""
    from app import main

    monkeypatch.setattr(main, "now", lambda: "2030-01-01T00:00:00Z")
    client.post("/cases", json=BODY)
    client.post("/cases", json=BODY)
    monkeypatch.setattr(main, "now", lambda: "2030-01-01T00:05:00Z")
    client.patch("/cases/C-1001", json={"status": "resolved"})
    assert [c["id"] for c in client.get("/cases", params={"since": "2030-01-01T00:01:00Z"}).json()] == ["C-1001"]
    assert len(client.get("/cases").json()) == 2  # omitted still means everything


def test_since_is_inclusive_so_a_write_in_the_cursor_second_is_never_lost(client, monkeypatch):
    """The cursor a client stores is the newest updated_at it has seen, and timestamps are
    only second-resolution. With a strict >, anything else written during that same second is
    dropped from that refetch and from every later one — the row is gone for good. Inclusive
    costs a duplicate the client was already going to get from the WS frame."""
    from app import main

    monkeypatch.setattr(main, "now", lambda: "2030-01-01T00:00:00Z")
    client.post("/cases", json=BODY)
    cursor = client.get("/cases").json()[0]["updated_at"]  # what a client would store
    client.post("/cases", json=BODY)  # same second as the cursor
    ids = [c["id"] for c in client.get("/cases", params={"since": cursor}).json()]
    assert ids == ["C-1002", "C-1001"]  # the tie is returned, not silently dropped

    client.post("/calls")
    call_cursor = client.get("/calls").json()[0]["updated_at"]
    client.post("/calls")
    assert [c["id"] for c in client.get("/calls", params={"since": call_cursor}).json()] == ["CALL-2", "CALL-1"]


def test_an_unparseable_since_is_422_not_an_empty_list(client):
    """A malformed cursor used to match no rows, which reads exactly like "nothing changed".
    A client would sit there refetching emptiness and never know."""
    client.post("/cases", json=BODY)
    for bad in ("yesterday", "2030-13-45", "", "1756000000"):
        assert client.get("/cases", params={"since": bad}).status_code == 422, bad
        assert client.get("/calls", params={"since": bad}).status_code == 422, bad


def test_calls_since_follows_every_write_to_the_call(client, monkeypatch):
    """A call changes when it is patched and when a transcript line lands; both have to move
    the cursor, or a reconnecting desk shows a call frozen mid-conversation."""
    from app import main

    monkeypatch.setattr(main, "now", lambda: "2030-01-01T00:00:00Z")
    client.post("/calls")
    client.post("/calls")
    monkeypatch.setattr(main, "now", lambda: "2030-01-01T00:05:00Z")
    client.patch("/calls/CALL-1", json={"status": "ended"})
    assert [c["id"] for c in client.get("/calls", params={"since": "2030-01-01T00:01:00Z"}).json()] == ["CALL-1"]
    monkeypatch.setattr(main, "now", lambda: "2030-01-01T00:09:00Z")
    client.post("/calls/CALL-2/transcript", json={"role": "user", "text": "hi"})
    assert [c["id"] for c in client.get("/calls", params={"since": "2030-01-01T00:06:00Z"}).json()] == ["CALL-2"]
    assert len(client.get("/calls").json()) == 2  # omitted still means everything


def test_two_cases_cannot_share_a_lookup_code(client):
    """The code is the only thing standing between a caller and someone else's case, so a
    duplicate is a disclosure. codes.new_code() retries on collision, but that check and the
    insert are not atomic — the unique index is the backstop the retry loop cannot be."""
    from sqlalchemy import update

    client.post("/cases", json=BODY)
    client.post("/cases", json=BODY)
    with db.connect() as conn:
        code = conn.execute(select(db.cases.c.lookup_code).where(
            db.cases.c.id == 1, db.cases.c.city_id == db.current_city_id())).scalar()
    with pytest.raises(IntegrityError):
        with db.connect() as conn:
            conn.execute(update(db.cases).where(db.cases.c.id == 2).values(lookup_code=code))
