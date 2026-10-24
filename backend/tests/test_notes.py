"""A note is a case_events row, not a column.

The old shape made add_note a GET then a PATCH of the whole string: two writers in the
same second lost one of the notes, and the audit log recorded a rewrite of the field
rather than the note itself. These tests pin the new shape and the refusal that pushes
callers onto it.
"""
from tests.test_cases import BODY


def test_note_is_appended_by_one_post_and_read_back_on_the_case(client):
    """One POST per note: no read-modify-write, so two staff appending at once keep both."""
    client.post("/cases", json=BODY)
    r = client.post("/cases/C-1001/notes", json={"text": "called the resident back"},
                    headers={"X-Source": "voice"})
    assert r.status_code == 201
    e = r.json()
    assert (e["case_id"], e["field"], e["old_value"], e["new_value"], e["source"]) == (
        "C-1001", "note", None, "called the resident back", "voice")
    assert e["ts"].endswith("Z")
    client.post("/cases/C-1001/notes", json={"text": "crew dispatched"})
    # the JSON field the dashboard and the agent still read: every note, oldest first
    assert client.get("/cases/C-1001").json()["notes"] == "called the resident back\ncrew dispatched"
    assert client.get("/cases").json()[0]["notes"] == "called the resident back\ncrew dispatched"


def test_notes_show_up_in_the_audit_trail_with_their_own_source(client):
    """A note used to be logged as a rewrite of the whole notes field; now the row IS the note,
    so staff see what was said and who said it, not a diff of a growing blob."""
    client.post("/cases", json=BODY)
    client.post("/cases/C-1001/notes", json={"text": "left a voicemail"}, headers={"X-Source": "voice"})
    notes = [e for e in client.get("/cases/C-1001/events").json() if e["field"] == "note"]
    assert [(e["new_value"], e["source"]) for e in notes] == [("left a voicemail", "voice")]


def test_a_note_bumps_updated_at_so_since_and_the_dashboard_see_it(client, monkeypatch):
    """updated_at is what both the ?since= cursor and the dashboard's row flash key off.
    A note that didn't bump it would be a change no client ever noticed."""
    from app import main

    client.post("/cases", json=BODY)
    before = client.get("/cases/C-1001").json()["updated_at"]
    monkeypatch.setattr(main, "now", lambda: "2030-01-01T00:00:00Z")
    client.post("/cases/C-1001/notes", json={"text": "crew dispatched"})
    after = client.get("/cases/C-1001").json()
    assert after["updated_at"] == "2030-01-01T00:00:00Z" != before


def test_patch_notes_is_refused_and_says_where_notes_went(client):
    """Ignoring an unknown field would swallow a staff edit silently. The 422 has to name
    the endpoint that replaced it, because the agent and the dashboard both still send this."""
    client.post("/cases", json=BODY)
    r = client.patch("/cases/C-1001", json={"notes": "crew dispatched"})
    assert r.status_code == 422
    assert "/cases/{case_id}/notes" in r.text
    assert client.get("/cases/C-1001").json()["notes"] == ""  # and nothing was written


def test_note_on_a_missing_case_is_404_and_writes_nothing(client):
    """Same 404 shape as every other case endpoint, and no orphan event row behind it."""
    r = client.post("/cases/C-9999/notes", json={"text": "x"})
    assert r.status_code == 404 and r.json()["detail"] == "case not found"
    assert client.post("/cases/garbage/notes", json={"text": "x"}).status_code == 404


def test_note_broadcasts_a_case_frame(client):
    """Writes broadcast: the case desk refetches on the frame, or the note sits unseen."""
    client.post("/cases", json=BODY)
    with client.websocket_connect("/ws") as ws:
        client.post("/cases/C-1001/notes", json={"text": "hello"})
        assert ws.receive_json() == {"type": "case", "id": "C-1001"}
