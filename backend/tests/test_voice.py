"""The ElevenLabs server tools (CONTRACT "## ElevenLabs voice").

These are the rules the LiveKit agent used to keep in memory. A webhook has no memory, so
each test drives the tools the way a call would and checks the rule still holds when every
step is a separate, stateless request.
"""
import hashlib
import hmac
import json
import time

import pytest

from app import elevenlabs

SECRET = "tool-secret"
HOOK_SECRET = "hook-secret"


@pytest.fixture
def voice(client, monkeypatch):
    monkeypatch.setenv("VOICE_TOOL_SECRET", SECRET)
    monkeypatch.setenv("ELEVENLABS_WEBHOOK_SECRET", HOOK_SECRET)
    call_id = client.post("/calls", json={}).json()["id"]

    def run(tool: str, **body) -> str:
        r = client.post(f"/voice/tools/{tool}", json={"call_id": call_id, **body},
                        headers={"X-Voice-Secret": SECRET})
        assert r.status_code == 200, r.text  # failures are sentences, never a status code
        return r.json()["result"]

    run.call_id = call_id
    return run


def file_case(voice, issue="pothole", description="Deep pothole on Elm St") -> str:
    assert voice("create_case", name="Maria Lopez", phone="555 123 4567").startswith("Started case")
    voice("update_case", issue_type=issue)
    return voice("update_case", description=description)


def code_of(result: str) -> str:
    return result.rsplit("Lookup code ", 1)[1]


def test_tools_refuse_without_the_shared_secret(client, monkeypatch):
    """The tools are on a public tunnel; without the secret anyone could file or read cases."""
    client.post("/calls", json={})
    body = {"call_id": "CALL-1", "name": "x", "phone": "5551234567"}
    monkeypatch.delenv("VOICE_TOOL_SECRET", raising=False)
    assert client.post("/voice/tools/create_case", json=body).status_code == 503
    monkeypatch.setenv("VOICE_TOOL_SECRET", SECRET)
    assert client.post("/voice/tools/create_case", json=body,
                       headers={"X-Voice-Secret": "wrong"}).status_code == 401
    assert client.get("/cases").json() == []


def test_lookup_code_is_withheld_until_the_case_is_filed(voice):
    """The code proves ownership; reading it before the case says what it's for gives the
    caller a code to a blank case."""
    assert voice("create_case", name="Maria Lopez", phone="5551234567") == "Started case C-1001"
    assert voice("update_case", issue_type="pothole") == "Updated case C-1001"
    filed = voice("update_case", description="Deep pothole on Elm St")
    assert filed.startswith("Updated case C-1001. Lookup code ") and code_of(filed).count("-") == 2


def test_a_filed_case_is_read_only_and_a_second_problem_opens_a_new_case(voice, client):
    """'My trash wasn't collected' after a pothole was filed must not rewrite the pothole."""
    file_case(voice)
    assert "already filed" in voice("update_case", issue_type="missed_pickup")
    assert client.get("/cases/C-1001").json()["issue_type"] == "pothole"
    assert voice("create_case", name="Maria Lopez", phone="5551234567") == "Started case C-1002"
    call = client.get(f"/calls/{voice.call_id}").json()
    assert call["case_id"] == "C-1002" and call["case_ids"] == ["C-1001", "C-1002"]


def test_a_second_case_waits_until_the_first_is_filed(voice, client):
    """Otherwise the agent can abandon a half-filled case with no type or description."""
    voice("create_case", name="Maria Lopez", phone="5551234567")
    assert voice("create_case", name="Maria Lopez", phone="5551234567").startswith("Finish the current case")
    assert len(client.get("/cases").json()) == 1


def test_partial_phone_numbers_are_refused(voice, client):
    """STT splits numbers across turns; a seven-digit case can never be called back."""
    assert "has 7 digits" in voice("create_case", name="Maria Lopez", phone="555 1234")
    assert client.get("/cases").json() == []


def test_bad_issue_type_comes_back_as_a_correctable_sentence(voice):
    voice("create_case", name="Maria Lopez", phone="5551234567")
    assert voice("update_case", issue_type="graffiti").startswith("issue_type must be exactly one of")


def test_lookup_finds_the_case_and_never_reads_its_code_back(voice, client):
    """A looked-up case belongs to the caller already; the code is not ours to re-issue,
    and the case must not be rewritable from a follow-up call."""
    code = code_of(file_case(voice))
    later = client.post("/calls", json={}).json()["id"]

    def run(tool, **body):
        return client.post(f"/voice/tools/{tool}", json={"call_id": later, **body},
                           headers={"X-Voice-Secret": SECRET}).json()["result"]

    found = run("lookup_case", code=code.replace("-", " and "))
    assert found.startswith("Case C-1001, pothole, status open")
    assert code not in found
    assert "already filed" in run("update_case", description="something else")
    assert run("add_note", note="Now two feet wide") == "Note added to case C-1001"
    assert client.get("/cases/C-1001").json()["notes"] == "Now two feet wide"
    events = client.get("/cases/C-1001/events").json()
    assert {e["source"] for e in events} == {"voice"}  # every write here came off a call


def test_a_looked_up_case_is_never_rewritten_even_if_unclassified(voice, client):
    """Staff can open a case with no issue type. A caller who looks it up is reaching
    someone's existing request, not filling in a new one, so update_case must refuse it."""
    created = client.post("/cases", json={"name": "Ana", "phone": "5551234567",
                                          "description": "walk-in"}).json()
    voice("lookup_case", code=created["lookup_code"])
    assert "already filed" in voice("update_case", issue_type="pothole")
    assert client.get(f"/cases/{created['id']}").json()["issue_type"] is None


def test_wrong_codes_are_rate_limited_per_call(voice):
    for _ in range(4):
        assert voice("lookup_case", code="no such code") == "No case matches that code."
    assert voice("lookup_case", code="no such code") == "Too many attempts."


def test_note_needs_a_case_on_the_call(voice):
    assert voice("add_note", note="hello").startswith("No case on this call yet")


def test_transfer_keeps_the_call_live_for_staff(voice, client):
    assert "Do not end the call" in voice("transfer_to_staff", reason="caller asked for a person")
    call = client.get(f"/calls/{voice.call_id}").json()
    assert call["status"] == "needs_person" and call["ended_at"] is None


def test_unknown_call_is_spoken_as_an_outage(client, monkeypatch):
    """A tool must never leave the agent silent, even on a call id it doesn't know."""
    monkeypatch.setenv("VOICE_TOOL_SECRET", SECRET)
    r = client.post("/voice/tools/add_note", json={"call_id": "CALL-99", "note": "x"},
                    headers={"X-Voice-Secret": SECRET})
    assert r.status_code == 200 and "couldn't reach" in r.json()["result"]


def test_session_needs_elevenlabs_config_and_opens_no_call_without_it(client, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_AGENT_ID", raising=False)
    assert client.post("/voice/session").status_code == 503
    assert client.get("/calls").json() == []


def test_session_returns_the_call_and_token(client, monkeypatch):
    async def fake_token():
        return "tok"

    monkeypatch.setattr(elevenlabs, "conversation_token", fake_token)
    r = client.post("/voice/session")
    assert r.status_code == 201 and r.json() == {"call_id": "CALL-1", "token": "tok"}
    assert client.get("/calls/CALL-1").json()["status"] == "active"


# ---- post-call webhook ----

def signed(body: dict, secret: str = HOOK_SECRET, ts: int | None = None) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    t = str(ts if ts is not None else int(time.time()))
    v0 = hmac.new(secret.encode(), t.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return raw, {"ElevenLabs-Signature": f"t={t},v0={v0}", "Content-Type": "application/json"}


def post_call(call_id: str, transcript=(), summary="Resident reported a pothole.") -> dict:
    return {"type": "post_call_transcription", "data": {
        "conversation_id": "conv_1",
        "transcript": [{"role": r, "message": m} for r, m in transcript],
        "analysis": {"transcript_summary": summary},
        "conversation_initiation_client_data": {"dynamic_variables": {"call_id": call_id}},
    }}


def test_post_call_writes_summary_and_ends_the_call(voice, client):
    raw, headers = signed(post_call(voice.call_id, [("agent", "Hello"), ("user", "Pothole")]))
    assert client.post("/voice/webhooks/post-call", content=raw, headers=headers).status_code == 200
    call = client.get(f"/calls/{voice.call_id}").json()
    assert call["status"] == "ended" and call["ended_at"]
    assert call["summary"] == "Resident reported a pothole."
    assert [(l["role"], l["text"]) for l in call["transcript"]] == [("agent", "Hello"), ("user", "Pothole")]


def test_post_call_does_not_duplicate_a_streamed_transcript(voice, client):
    """The browser already relayed the lines live; backfilling again would show every line twice."""
    client.post(f"/calls/{voice.call_id}/transcript", json={"role": "user", "text": "Pothole"})
    raw, headers = signed(post_call(voice.call_id, [("agent", "Hello"), ("user", "Pothole")]))
    client.post("/voice/webhooks/post-call", content=raw, headers=headers)
    assert len(client.get(f"/calls/{voice.call_id}").json()["transcript"]) == 1


def test_post_call_rejects_bad_and_stale_signatures(voice, client):
    raw, headers = signed(post_call(voice.call_id), secret="wrong")
    assert client.post("/voice/webhooks/post-call", content=raw, headers=headers).status_code == 401
    raw, headers = signed(post_call(voice.call_id), ts=int(time.time()) - 31 * 60)
    assert client.post("/voice/webhooks/post-call", content=raw, headers=headers).status_code == 401
    assert client.get(f"/calls/{voice.call_id}").json()["status"] == "active"


def test_post_call_for_an_unknown_call_is_acknowledged(voice, client):
    """A non-2xx makes ElevenLabs retry and eventually disable the webhook."""
    raw, headers = signed(post_call("CALL-99"))
    assert client.post("/voice/webhooks/post-call", content=raw, headers=headers).status_code == 200
