"""One call can touch several cases: call_cases is the truth, calls.case_id is the cursor."""
from tests.test_cases import BODY


def link(client, call_id, case_id, how="created"):
    return client.post(f"/calls/{call_id}/cases", json={"case_id": case_id, "how": how},
                       headers={"X-Source": "voice"})


def test_linking_the_same_case_twice_is_idempotent(client):
    """The agent retries a failed link, so the second POST must not duplicate the link or
    write a second call_linked row into the case's audit trail."""
    client.post("/cases", json=BODY)
    client.post("/calls")
    assert link(client, "CALL-1", "C-1001").status_code == 201
    r = link(client, "CALL-1", "C-1001")
    assert r.status_code == 200 and r.json()["case_ids"] == ["C-1001"]
    linked = [e for e in client.get("/cases/C-1001/events").json() if e["field"] == "call_linked"]
    assert [(e["new_value"], e["source"]) for e in linked] == [("CALL-1", "voice")]


def test_unknown_call_or_case_is_404(client):
    """Same 404-before-write rule as every other link path; a typo must not create a dangling row."""
    client.post("/calls")
    assert link(client, "CALL-1", "C-9999").status_code == 404
    client.post("/cases", json=BODY)
    assert link(client, "CALL-9", "C-1001").status_code == 404


def test_two_cases_on_one_call_are_both_kept(client):
    """A caller reports a pothole and a missed pickup in one call: both cases must show that
    call, and the call must list both in the order they were opened."""
    client.post("/cases", json=BODY)
    client.post("/cases", json={**BODY, "issue_type": "missed_pickup"})
    client.post("/calls")
    assert link(client, "CALL-1", "C-1001").status_code == 201
    r = link(client, "CALL-1", "C-1002")
    assert r.status_code == 201
    assert r.json()["case_ids"] == ["C-1001", "C-1002"]
    assert r.json()["case_id"] == "C-1002"  # the cursor follows the case being worked now
    assert [c["id"] for c in client.get("/cases/C-1001/calls").json()] == ["CALL-1"]
    assert [c["id"] for c in client.get("/cases/C-1002/calls").json()] == ["CALL-1"]
    assert client.get("/calls/CALL-1").json()["case_ids"] == ["C-1001", "C-1002"]
    assert client.get("/calls").json()[0]["case_ids"] == ["C-1001", "C-1002"]


def test_patch_case_id_populates_the_join(client):
    """lookup_case still links by PATCH; the case page reads call_cases, so the old agent path
    has to land there too — and re-sending the same case must not log a second event."""
    client.post("/cases", json=BODY)
    client.post("/calls")
    assert client.patch("/calls/CALL-1", json={"case_id": "C-1001"}).json()["case_ids"] == ["C-1001"]
    client.patch("/calls/CALL-1", json={"case_id": "C-1001"})
    assert [c["id"] for c in client.get("/cases/C-1001/calls").json()] == ["CALL-1"]
    linked = [e for e in client.get("/cases/C-1001/events").json() if e["field"] == "call_linked"]
    assert len(linked) == 1
