"""Containment: the share of calls the agent handled with nobody from the city on the line.

It is the number the product is judged on, so what counts as "contained" is worth pinning
down: a call is contained when it ENDED and nobody ever asked for a person.
"""


def call_at(client, monkeypatch, ts: str) -> str:
    """A call started at a fixed second, so the window bounds are testable."""
    from app import main

    monkeypatch.setattr(main, "now", lambda: ts)
    return client.post("/calls").json()["id"]


def test_containment_counts_the_five_kinds_of_call(client, monkeypatch):
    """The mix a real window holds. Every call lands in exactly one bucket, and only the
    two settled ones are scored: a call still on the line has not been contained OR
    transferred yet, so counting it either way would move the number for no reason."""
    inside = "2030-06-10T12:00:00Z"
    contained_1 = call_at(client, monkeypatch, inside)
    contained_2 = call_at(client, monkeypatch, inside)
    transferred = call_at(client, monkeypatch, inside)
    waiting = call_at(client, monkeypatch, inside)
    live = call_at(client, monkeypatch, inside)
    call_at(client, monkeypatch, "2030-06-01T12:00:00Z")  # before the window
    call_at(client, monkeypatch, "2030-06-20T12:00:00Z")  # after it

    for call_id in (contained_1, contained_2):
        client.patch(f"/calls/{call_id}", json={"status": "ended"})
    # the agent's transfer_to_staff, then the hang-up: the marker has to survive the end
    client.patch(f"/calls/{transferred}", json={"status": "needs_person", "transfer_reason": "angry"})
    client.patch(f"/calls/{transferred}", json={"status": "ended"})
    client.patch(f"/calls/{waiting}", json={"status": "needs_person", "transfer_reason": "wants a person"})
    assert live  # still active, untouched

    body = client.get("/stats", params={"since": "2030-06-05T00:00:00Z",
                                        "until": "2030-06-15T00:00:00Z"}).json()
    assert body["calls"] == 5  # the two outside the window are not in any bucket
    assert body["contained"] == 2
    assert body["needs_person"] == 2  # the ended-after-transfer one still counts as transferred
    assert body["active"] == 1
    assert body["containment"] == 2 / 4  # active excluded from the denominator


def test_a_transfer_still_counts_after_the_call_ends(client, monkeypatch):
    """PATCH status=ended overwrites needs_person, so status alone cannot answer this:
    without transfer_reason surviving, every transferred call would look contained and
    containment would read 100%."""
    call_id = call_at(client, monkeypatch, "2030-06-10T12:00:00Z")
    client.patch(f"/calls/{call_id}", json={"status": "needs_person", "transfer_reason": "wants a person"})
    client.patch(f"/calls/{call_id}", json={"status": "ended"})
    assert client.get(f"/calls/{call_id}").json()["transfer_reason"] == "wants a person"

    body = client.get("/stats").json()
    assert (body["contained"], body["needs_person"]) == (0, 1)
    assert body["containment"] == 0.0


def test_an_empty_window_is_null_not_zero(client, monkeypatch):
    """0.0 is a real score — it means every call was transferred. A window with nothing to
    judge has to be distinguishable from that, or a quiet Sunday reads as total failure."""
    assert client.get("/stats").json() == {"calls": 0, "contained": 0, "needs_person": 0,
                                           "active": 0, "containment": None}
    # an active-only window is empty for scoring purposes too: nothing has settled yet
    call_at(client, monkeypatch, "2030-06-10T12:00:00Z")
    body = client.get("/stats").json()
    assert (body["calls"], body["active"], body["containment"]) == (1, 1, None)


def test_the_window_is_on_started_at_and_inclusive(client, monkeypatch):
    """On started_at, not updated_at: a window's answer has to stop moving once the window
    is over, and every later write to a call bumps updated_at. Inclusive for the same reason
    ?since= is — second-resolution timestamps drop the boundary row under a strict compare."""
    from app import main

    edge = "2030-06-10T12:00:00Z"
    call_id = call_at(client, monkeypatch, edge)
    monkeypatch.setattr(main, "now", lambda: "2030-07-01T00:00:00Z")
    client.patch(f"/calls/{call_id}", json={"status": "ended"})  # touched long after the window

    assert client.get("/stats", params={"since": edge}).json()["calls"] == 1
    assert client.get("/stats", params={"until": edge}).json()["calls"] == 1
    assert client.get("/stats", params={"since": "2030-06-10T12:00:01Z"}).json()["calls"] == 0
    assert client.get("/stats", params={"until": "2030-06-10T11:59:59Z"}).json()["calls"] == 0


def test_an_unparseable_bound_is_422_not_a_silent_empty_window(client):
    """Same reasoning as the ?since= cursor: matching nothing looks exactly like a quiet
    week, and 0 calls would be reported as a real containment number of null forever."""
    for bad in ("yesterday", "2030-13-45", "", "1756000000"):
        assert client.get("/stats", params={"since": bad}).status_code == 422, bad
        assert client.get("/stats", params={"until": bad}).status_code == 422, bad
