"""Who acted, not just which system acted.

`source` has only ever distinguished the voice agent from the dashboard, so every staff
write in the log looked the same. `actor` names the person. It comes off the X-Actor
header, which the backend trusts — there is no backend auth yet — so these tests pin the
bounds it is trusted within, and pin that its absence stays a null rather than a guess.
"""
from tests.test_cases import BODY

NEIL = {"X-Actor": "neil"}


def events(client, case_id="C-1001"):
    return client.get(f"/cases/{case_id}/events").json()


def test_a_note_and_a_patch_both_record_who_sent_them(client):
    """The two writes staff actually make from the dashboard. Both go through the same
    event helper, so if either lost the actor the history would name a system, not a person."""
    client.post("/cases", json=BODY)
    r = client.post("/cases/C-1001/notes", json={"text": "called the resident back"}, headers=NEIL)
    assert r.status_code == 201 and r.json()["actor"] == "neil"
    client.patch("/cases/C-1001", json={"status": "in_progress"}, headers=NEIL)
    assert [(e["field"], e["source"], e["actor"]) for e in events(client)] == [
        ("created", "staff", None), ("note", "staff", "neil"), ("status", "staff", "neil")]


def test_no_header_means_no_actor_not_an_empty_name(client):
    """The agent never sends X-Actor, and dev runs with auth off don't either. A null says
    "nobody named"; an empty string would render as a person whose name is blank."""
    client.post("/cases", json=BODY)
    client.post("/cases/C-1001/notes", json={"text": "left a voicemail"},
                headers={"X-Source": "voice"})
    note = [e for e in events(client) if e["field"] == "note"][0]
    assert (note["source"], note["actor"]) == ("voice", None)


def test_an_empty_or_blank_header_is_stored_as_null(client):
    """A signed-out client that sends the header anyway must not write a blank actor."""
    client.post("/cases", json=BODY, headers={"X-Actor": ""})
    client.post("/cases/C-1001/notes", json={"text": "x"}, headers={"X-Actor": "   "})
    assert [e["actor"] for e in events(client)] == [None, None]


def test_a_long_actor_is_truncated_to_64_characters(client):
    """The header is trusted, so what it can write into the audit log is bounded here."""
    client.post("/cases", json=BODY)
    client.post("/cases/C-1001/notes", json={"text": "x"}, headers={"X-Actor": "n" * 100})
    note = [e for e in events(client) if e["field"] == "note"][0]
    assert note["actor"] == "n" * 64


def test_linking_a_case_to_a_call_records_the_actor_too(client):
    """The link event is written by a different helper than the note; staff dragging a case
    onto a call from the dashboard is still a person acting."""
    client.post("/cases", json=BODY)
    client.post("/calls", json={})
    client.post("/calls/CALL-1/cases", json={"case_id": "C-1001"}, headers=NEIL)
    linked = [e for e in events(client) if e["field"] == "call_linked"][0]
    assert linked["actor"] == "neil"
