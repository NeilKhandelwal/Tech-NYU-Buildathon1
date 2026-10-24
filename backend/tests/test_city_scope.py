"""One deployment serves one city, and nothing it reads may come from another one.

The scope itself is proved by the whole suite: tests/conftest.py fails any SELECT that
reaches cases, calls or case_events without a city_id criterion. These tests are the two
things that guard cannot prove about itself — that it still bites, and that the filter it
forces actually hides another city's rows.
"""
import pytest
from sqlalchemy import insert, select

from app import db
from tests.conftest import UnscopedRead
from tests.test_cases import BODY


def test_the_guard_still_catches_a_read_that_forgot_the_city(client):
    """A guard that stopped firing would let every other test pass while the scope leaked,
    so the suite has to watch the watcher: this is the query it exists to reject."""
    with pytest.raises(UnscopedRead):
        with db.connect() as conn:
            conn.execute(select(db.cases)).fetchall()


def test_a_read_that_is_meant_to_be_city_wide_says_so_and_is_allowed(client):
    """The exemption is deliberate and narrow: lookup_code is unique across the whole
    table, so its collision check must see every city or the insert fails the index."""
    with db.connect() as conn:
        conn.execute(
            select(db.cases.c.id).where(db.cases.c.lookup_code == "no-such-code")
            .execution_options(city_scope_exempt="lookup codes are unique across all cities")
        ).fetchall()


def test_another_citys_case_is_invisible_on_every_read(client, monkeypatch):
    """The whole point of the column: two cities on one database must not see each other's
    residents. A case filed by city 1 is not in city 2's list, cannot be fetched by id, and
    cannot be reached with its lookup code — which is the disclosure that would matter."""
    with db.connect() as conn:
        conn.execute(insert(db.cities).values(id=2, name="Other City"))

    filed = client.post("/cases", json=BODY).json()  # CITY_ID unset: the seeded city, 1
    code = filed["lookup_code"]
    client.post("/calls")

    monkeypatch.setenv("CITY_ID", "2")
    assert client.get("/cases").json() == []
    assert client.get("/calls").json() == []
    assert client.get(f"/cases/{filed['id']}").status_code == 404
    assert client.get(f"/cases/{filed['id']}/events").status_code == 404
    assert client.get(f"/cases/{filed['id']}/calls").status_code == 404
    assert client.get("/cases/lookup", params={"code": code}).status_code == 404

    # and city 2 writes into its own scope, which city 1 in turn cannot see
    other = client.post("/cases", json=BODY).json()
    assert [c["id"] for c in client.get("/cases").json()] == [other["id"]]
    monkeypatch.delenv("CITY_ID")
    assert [c["id"] for c in client.get("/cases").json()] == [filed["id"]]


def test_city_id_is_not_in_any_response(client):
    """Internal: which city a row belongs to is the deployment's business, not the API's."""
    case = client.post("/cases", json=BODY).json()
    client.post("/calls")
    client.post(f"/cases/{case['id']}/notes", json={"text": "hello"})
    for payload in (case, client.get("/cases").json()[0], client.get("/calls").json()[0],
                    client.get(f"/cases/{case['id']}/events").json()[0]):
        assert "city_id" not in payload
