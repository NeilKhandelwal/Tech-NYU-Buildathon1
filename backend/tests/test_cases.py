BODY = {"name": "Maria Lopez", "phone": "(555) 123-4567", "issue_type": "missed_pickup",
        "description": "Trash not collected on Elm St Tuesday"}


def test_create_returns_contract_shape(client):
    """The voice agent reads the id aloud and the dashboard filters by phone,
    so the id format and digits-only phone are load-bearing, not cosmetic."""
    r = client.post("/cases", json=BODY)
    assert r.status_code == 201
    case = r.json()
    assert case["id"] == "C-1001"
    assert case["phone"] == "5551234567"
    assert case["status"] == "open" and case["notes"] == ""
    assert case["created_at"].endswith("Z") and case["created_at"] == case["updated_at"]


def test_list_newest_first_and_phone_filter(client):
    """lookup_case(phone) takes the first result as 'most recent', so order matters;
    the filter must match on digits regardless of how the caller formatted the number."""
    client.post("/cases", json=BODY)
    client.post("/cases", json={**BODY, "phone": "5559999999"})
    client.post("/cases", json=BODY)
    assert [c["id"] for c in client.get("/cases").json()] == ["C-1003", "C-1002", "C-1001"]
    filtered = client.get("/cases", params={"phone": "555-123-4567"}).json()
    assert [c["id"] for c in filtered] == ["C-1003", "C-1001"]


def test_get_missing_is_404(client):
    """Agent must be able to tell a caller 'no such case' rather than crash on a bad id."""
    assert client.get("/cases/C-9999").status_code == 404
    assert client.get("/cases/garbage").status_code == 404


def test_patch_updates_status_and_bumps_updated_at(client, monkeypatch):
    """Dashboard polls and shows updated_at; a status change that doesn't bump it is invisible."""
    from app import main
    created = client.post("/cases", json=BODY).json()
    monkeypatch.setattr(main, "now", lambda: "2030-01-01T00:00:00Z")
    r = client.patch("/cases/C-1001", json={"status": "in_progress"})
    assert r.status_code == 200
    assert r.json()["status"] == "in_progress"
    assert r.json()["updated_at"] == "2030-01-01T00:00:00Z"
    assert r.json()["created_at"] == created["created_at"]
    assert r.json()["description"] == BODY["description"]  # untouched fields survive


def test_patch_invalid_status_is_422(client):
    """Status drives dashboard columns; an unknown value would silently vanish from the board."""
    client.post("/cases", json=BODY)
    assert client.patch("/cases/C-1001", json={"status": "done"}).status_code == 422
    assert client.patch("/cases/C-9999", json={"status": "resolved"}).status_code == 404


def test_issue_type_is_null_until_classified(client):
    """The agent opens a case before it knows the type; null must survive the round trip so the
    dashboard shows "not classified yet" instead of flashing a wrong "Other"."""
    body = {k: v for k, v in BODY.items() if k != "issue_type"}
    r = client.post("/cases", json=body)
    assert r.status_code == 201 and r.json()["issue_type"] is None
    assert client.get("/cases").json()[0]["issue_type"] is None

    assert client.patch("/cases/C-1001", json={"issue_type": "pothole"}).json()["issue_type"] == "pothole"
    ev = [e for e in client.get("/cases/C-1001/events").json() if e["field"] == "issue_type"]
    assert len(ev) == 1 and ev[0]["old_value"] is None and ev[0]["new_value"] == "pothole"
