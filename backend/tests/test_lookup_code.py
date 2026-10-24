"""The lookup code is the only thing standing between a caller and someone else's case.

A phone number is public knowledge and a case ID is four digits, so both are discovery,
not proof. These tests pin the two halves of that: the code is spoken back exactly once,
and it never appears in any other response.
"""
import pytest

from app.words import WORDS
from tests.test_cases import BODY


@pytest.fixture(autouse=True)
def fresh_limiter():
    # the miss counter is module-level, so unlike the DB it outlives a single test
    from app import main
    main.lookup_misses.clear()


def create(client, **kw) -> dict:
    return client.post("/cases", json={**BODY, **kw}).json()


def test_create_returns_three_words_from_the_list(client):
    """The agent reads these words aloud; a word off the list would be one STT can't recover."""
    case = create(client)
    words = case["lookup_code"].split("-")
    assert len(words) == 3
    assert all(w in WORDS for w in words)


def test_code_is_returned_on_create_and_nowhere_else(client):
    """Anyone can GET a case list; if the code rode along on those, it would stop being a secret."""
    create(client)
    assert "lookup_code" not in client.get("/cases/C-1001").json()
    assert "lookup_code" not in client.get("/cases").json()[0]
    assert "lookup_code" not in client.get("/cases", params={"phone": BODY["phone"]}).json()[0]
    found = client.get("/cases/lookup", params={"code": create(client)["lookup_code"]}).json()
    assert "lookup_code" not in found


def test_lookup_normalizes_what_a_caller_says(client):
    """Callers say "Blue River, Maple" and "blue and river dash maple"; both are the same code."""
    code = create(client)["lookup_code"]
    spoken = code.replace("-", " ").title()
    assert client.get("/cases/lookup", params={"code": spoken}).json()["id"] == "C-1001"
    a, b, c = code.split("-")
    assert client.get("/cases/lookup", params={"code": f"{a} and {b} dash {c}"}).json()["id"] == "C-1001"
    assert client.get("/cases/lookup", params={"code": f" {a}, {b}, {c} "}).json()["id"] == "C-1001"


def test_unknown_and_malformed_codes_get_the_same_404(client):
    """Different answers would tell a guesser which half of the code was right."""
    create(client)
    for code in ("blue-river-maple-extra", "not a real code", "", "%%%"):
        r = client.get("/cases/lookup", params={"code": code})
        assert r.status_code == 404 and r.json()["detail"] == "no case for that code"


def test_fifth_miss_on_one_call_is_rate_limited(client):
    """27M codes is only safe if guessing is slow; one call gets a handful of tries, not a keyspace."""
    create(client)
    headers = {"X-Call-Id": "CALL-1"}
    for _ in range(4):
        assert client.get("/cases/lookup", params={"code": "wrong-code-here"}, headers=headers).status_code == 404
    r = client.get("/cases/lookup", params={"code": "wrong-code-here"}, headers=headers)
    assert r.status_code == 429 and r.json()["detail"] == "too many attempts"
    # another call is a different counter: one guesser must not lock out the next caller
    assert client.get("/cases/lookup", params={"code": "wrong-code-here"},
                      headers={"X-Call-Id": "CALL-2"}).status_code == 404


def test_successful_lookup_is_audited(client):
    """Staff need to see that a case was disclosed on a call, not just that it was edited."""
    code = create(client)["lookup_code"]
    client.get("/cases/lookup", params={"code": code},
               headers={"X-Call-Id": "CALL-9", "X-Source": "voice"})
    e = client.get("/cases/C-1001/events").json()[-1]
    assert (e["field"], e["new_value"], e["source"]) == ("looked_up", "CALL-9", "voice")
