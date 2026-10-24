"""Lane B webhooks (app/webhooks.py): the post-call webhook is how a submitted case reaches
the caseworker with an English transcript, summary, priority and building history."""
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import hc, hcdb


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("HC_DATABASE_URL", f"sqlite:///{tmp_path / 'hc.db'}")
    for k in ("XAI_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_WEBHOOK_SECRET",
              "ELEVENLABS_CALLBACK_AGENT_ID"):
        monkeypatch.delenv(k, raising=False)
    hcdb.reset_engine()
    hcdb.metadata.create_all(hcdb.engine())
    yield TestClient(hc.app)
    hcdb.reset_engine()


def case_with_conversation(status="submitted", **over):
    case = hcdb.insert_case(**{"case_number": "HC-654321", "resident_phone": "5551234567",
                               "language": "en", "address": "2800 Creston Ave, Bronx",
                               "indoor_f": 61, "is_violation": True, "days_without_heat": 3,
                               "status": status, **over})
    hcdb.add_event(case["id"], "intake_call", "intake_agent", payload={"conversation_id": "conv-9"})
    return case


def post_call(conversation_id="conv-9", **data):
    return {"type": "post_call_transcription", "data": {
        "conversation_id": conversation_id, "agent_id": "agent-intake",
        "transcript": [{"role": "agent", "message": "Hello, this is Heat Check."},
                       {"role": "user", "message": "My heat is out."}],
        "analysis": {"transcript_summary": "Resident reports no heat."}, **data}}


def test_post_call_fills_the_case_and_moves_it_to_review(api):
    case = case_with_conversation()
    assert api.post("/api/webhooks/elevenlabs_post_call", json=post_call()).json() == {"ok": True}
    got = hcdb.get_case(case["id"])
    assert got["status"] == "in_review"
    assert got["transcript_original"] == "Agent: Hello, this is Heat Check.\nResident: My heat is out."
    assert got["transcript_en"] == got["transcript_original"]  # English call, no LLM needed
    assert got["summary_en"] == "Resident reports no heat."  # ElevenLabs summary fallback
    assert got["priority"] == "urgent"  # violation, three days without heat
    assert got["building"]["source"] == "seed" and got["building"]["repeat_offender"]
    assert got["suggested_action"] == "schedule_inspection"


def test_unconfirmed_case_stays_in_intake(api):
    """Only the resident's yes submits; a post-call webhook must not push a 'no' to the city."""
    case = case_with_conversation(status="intake")
    api.post("/api/webhooks/elevenlabs_post_call", json=post_call())
    assert hcdb.get_case(case["id"])["status"] == "intake"


def test_case_found_by_caller_phone_when_conversation_unknown(api):
    case = case_with_conversation()
    body = post_call("conv-other", metadata={"phone_call": {"external_number": "+15551234567"}})
    api.post("/api/webhooks/elevenlabs_post_call", json=body)
    assert hcdb.get_case(case["id"])["status"] == "in_review"


def test_unknown_call_is_ignored_with_200(api):
    """A non-2xx makes ElevenLabs retry forever; a call we don't know is not an error."""
    assert api.post("/api/webhooks/elevenlabs_post_call", json=post_call("nope")).status_code == 200


def test_callback_without_an_answer_is_no_answer(api, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_CALLBACK_AGENT_ID", "agent-callback")
    case = case_with_conversation(status="callback_requested")
    api.post("/api/webhooks/elevenlabs_post_call", json=post_call(agent_id="agent-callback"))
    got = hcdb.get_case(case["id"])
    assert got["resident_response"] == "no_answer" and got["status"] == "callback_requested"
    assert "callback_call" in [e["type"] for e in hcdb.list_events(case["id"])]


def test_callback_keeps_a_recorded_answer(api, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_CALLBACK_AGENT_ID", "agent-callback")
    case = case_with_conversation(status="called_back", resident_response="confirmed")
    api.post("/api/webhooks/elevenlabs_post_call", json=post_call(agent_id="agent-callback"))
    assert hcdb.get_case(case["id"])["resident_response"] == "confirmed"


def test_signature_is_checked_when_a_secret_is_set(api, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_WEBHOOK_SECRET", "s")
    raw = json.dumps(post_call()).encode()
    assert api.post("/api/webhooks/elevenlabs_post_call", content=raw).status_code == 401
    ts = str(int(time.time()))
    sig = hmac.new(b"s", ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
    r = api.post("/api/webhooks/elevenlabs_post_call", content=raw,
                 headers={"ElevenLabs-Signature": f"t={ts},v0={sig}"})
    assert r.status_code == 200


def test_inbound_sms_lands_on_the_case(api):
    case = case_with_conversation()
    r = api.post("/api/webhooks/twilio_sms", content="From=%2B15551234567&Body=Thank+you",
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200 and "<Response>" in r.text
    event = hcdb.list_events(case["id"])[-1]
    assert (event["type"], event["text_original"], event["text_en"]) == ("sms_in", "Thank you", "Thank you")
