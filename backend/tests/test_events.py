from tests.test_cases import BODY


def test_create_writes_created_event(client):
    """A case's audit trail must start at creation, or the dashboard can't show who opened it."""
    client.post("/cases", json=BODY, headers={"X-Source": "voice"})
    events = client.get("/cases/C-1001/events").json()
    assert len(events) == 1
    e = events[0]
    assert (e["field"], e["old_value"], e["new_value"], e["source"]) == ("created", None, "C-1001", "voice")
    assert e["case_id"] == "C-1001" and e["ts"].endswith("Z")


def test_patch_logs_only_changed_fields_with_source(client):
    """Staff read the log to see what changed; re-sent identical values would be noise, and
    source tells them whether the voice agent or a person made the edit."""
    client.post("/cases", json=BODY)
    r = client.patch("/cases/C-1001", headers={"X-Source": "voice"},
                     json={"status": "in_progress", "description": "crew dispatched",
                           "issue_type": "missed_pickup"})
    assert r.status_code == 200
    events = client.get("/cases/C-1001/events").json()[1:]  # skip "created"
    assert [(e["field"], e["old_value"], e["new_value"], e["source"]) for e in events] == [
        ("status", "open", "in_progress", "voice"),
        ("description", BODY["description"], "crew dispatched", "voice"),
    ]


def test_events_unknown_case_is_404(client):
    """Same 404 shape as every other case endpoint, so the dashboard's error handling is uniform."""
    r = client.get("/cases/C-9999/events")
    assert r.status_code == 404 and r.json()["detail"] == "case not found"
