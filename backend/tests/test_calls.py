from tests.test_cases import BODY


def test_call_lifecycle_sets_ended_at(client):
    """The agent opens a call, streams lines, then ends it; ended_at is what drops it from 'Live calls'."""
    r = client.post("/calls", json={})
    assert r.status_code == 201
    call = r.json()
    assert call == {"id": "CALL-1", "case_id": None, "case_ids": [], "status": "active",
                    "started_at": call["started_at"], "ended_at": None, "room": None,
                    "updated_at": call["started_at"], "summary": None, "transfer_reason": None}
    assert client.post("/calls/CALL-1/transcript", json={"role": "user", "text": "hi"}).status_code == 201
    ended = client.patch("/calls/CALL-1", json={"status": "ended"}).json()
    assert ended["status"] == "ended" and ended["ended_at"].endswith("Z")
    assert client.get("/calls", params={"status": "active"}).json() == []
    assert [c["id"] for c in client.get("/calls").json()] == ["CALL-1"]


def test_patch_summary_is_readable_back(client):
    """Staff read the post-call summary instead of the whole transcript, so it must survive the write."""
    client.post("/calls")
    assert client.get("/calls/CALL-1").json()["summary"] is None
    r = client.patch("/calls/CALL-1", json={"summary": "Resident reported a pothole; case C-1001 created."})
    assert r.status_code == 200 and r.json()["summary"] == "Resident reported a pothole; case C-1001 created."
    assert client.get("/calls/CALL-1").json()["summary"] == "Resident reported a pothole; case C-1001 created."
    assert client.patch("/calls/CALL-9", json={"summary": "x"}).status_code == 404


def test_get_call_includes_transcript_in_order(client):
    """/calls/[id] renders the conversation; out-of-order lines would read as nonsense."""
    client.post("/calls")
    for i, role in enumerate(["user", "agent", "user"]):
        r = client.post("/calls/CALL-1/transcript", json={"role": role, "text": f"line {i}"})
        assert r.status_code == 201 and r.json()["call_id"] == "CALL-1"
    lines = client.get("/calls/CALL-1").json()["transcript"]
    assert [(l["role"], l["text"]) for l in lines] == [("user", "line 0"), ("agent", "line 1"), ("user", "line 2")]
    assert client.get("/calls/CALL-9").status_code == 404
    assert client.post("/calls/CALL-9/transcript", json={"role": "user", "text": "x"}).status_code == 404


def test_patch_unknown_case_id_is_404(client):
    """Linking a call to a case the agent mistyped would orphan the transcript on the case page."""
    client.post("/calls")
    r = client.patch("/calls/CALL-1", json={"case_id": "C-9999"})
    assert r.status_code == 404 and r.json()["detail"] == "case not found"
    client.post("/cases", json=BODY)
    assert client.patch("/calls/CALL-1", json={"case_id": "C-1001"}).json()["case_id"] == "C-1001"


def test_case_calls_oldest_first_with_transcript(client):
    """Case detail shows the history of calls about that case, in the order they happened."""
    client.post("/cases", json=BODY)
    for _ in range(3):
        client.post("/calls")
    client.patch("/calls/CALL-2", json={"case_id": "C-1001"})
    client.patch("/calls/CALL-1", json={"case_id": "C-1001"})
    client.post("/calls/CALL-2/transcript", json={"role": "agent", "text": "hello"})
    calls = client.get("/cases/C-1001/calls").json()
    assert [c["id"] for c in calls] == ["CALL-1", "CALL-2"]
    assert calls[1]["transcript"][0]["text"] == "hello" and calls[0]["transcript"] == []
    assert client.get("/cases/C-9999/calls").status_code == 404


def test_ws_frame_after_write(client):
    """Dashboard refetches on any frame; without it, live calls lag behind the 2s poll."""
    with client.websocket_connect("/ws") as ws:
        client.post("/cases", json=BODY)
        assert ws.receive_json() == {"type": "case", "id": "C-1001"}
        client.post("/calls")
        client.post("/calls/CALL-1/transcript", json={"role": "user", "text": "hi"})
        assert ws.receive_json() == {"type": "call", "id": "CALL-1"}
        assert ws.receive_json() == {"type": "transcript", "id": "CALL-1"}


def test_call_found_by_room(client):
    """The browser /call page only knows its LiveKit room name; that's how it finds its transcript."""
    r = client.post("/calls", json={"room": "call-abc123"})
    assert r.status_code == 201 and r.json()["room"] == "call-abc123"
    client.post("/calls", json={"room": "call-other"})
    assert [c["id"] for c in client.get("/calls", params={"room": "call-abc123"}).json()] == ["CALL-1"]
    assert client.get("/calls", params={"room": "call-abc123", "status": "ended"}).json() == []
    assert client.get("/calls", params={"room": "nope"}).json() == []


def test_token_returns_room_scoped_credentials(client, monkeypatch):
    """The browser can't hold the API secret, so a wrong-shaped /token response means no call at all."""
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "devsecret" * 4)
    body = client.get("/token", params={"identity": "browser"}).json()
    assert body["url"] == "wss://example.livekit.cloud"
    assert body["room"].startswith("call-") and body["token"].count(".") == 2


def test_needs_person_is_a_live_status_not_an_ended_one(client):
    """A transferred call is still on the line: an ended_at here would drop it from 'Live calls'
    and staff would never see the caller waiting."""
    client.post("/calls")
    r = client.patch("/calls/CALL-1", json={"status": "needs_person",
                                           "transfer_reason": "caller asked for a person"})
    assert r.status_code == 200
    call = r.json()
    assert call["status"] == "needs_person" and call["ended_at"] is None
    assert call["transfer_reason"] == "caller asked for a person"
    assert [c["id"] for c in client.get("/calls", params={"status": "needs_person"}).json()] == ["CALL-1"]
    assert client.get("/calls", params={"status": "active"}).json() == []


def test_transfer_reason_survives_pickup_and_hangup(client):
    """Staff pick up (back to active) and later hang up; the reason is the record of why
    the agent handed off, so it must outlive both writes."""
    client.post("/calls")
    client.patch("/calls/CALL-1", json={"status": "needs_person", "transfer_reason": "upset caller"})
    picked_up = client.patch("/calls/CALL-1", json={"status": "active"}).json()
    assert picked_up["status"] == "active" and picked_up["ended_at"] is None
    ended = client.patch("/calls/CALL-1", json={"status": "ended"}).json()
    assert ended["status"] == "ended" and ended["ended_at"].endswith("Z")
    assert ended["transfer_reason"] == "upset caller"


def test_patch_cannot_reopen_an_ended_call(client):
    """An ended call is the record of what happened; reopening it would resurrect a dead call in
    'Live calls' and leave a stale ended_at, so the write is refused instead of half-applied."""
    client.post("/calls")
    ended = client.patch("/calls/CALL-1", json={"status": "ended"}).json()
    r = client.patch("/calls/CALL-1", json={"status": "active"})
    assert r.status_code == 409 and r.json()["detail"] == "call already ended"
    assert client.patch("/calls/CALL-1", json={"status": "needs_person"}).status_code == 409
    after = client.get("/calls/CALL-1").json()
    assert after["status"] == "ended" and after["ended_at"] == ended["ended_at"]
