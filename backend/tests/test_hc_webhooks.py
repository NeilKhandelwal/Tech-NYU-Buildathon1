"""Lane B webhooks (app/webhooks.py): the post-call webhook is how a submitted case reaches
the caseworker with an English transcript, summary, priority and building history."""
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import building, hc, hcdb


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("HC_DATABASE_URL", f"sqlite:///{tmp_path / 'hc.db'}")
    for k in ("XAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_WEBHOOK_SECRET",
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


def test_enrich_fills_building_and_priority_before_the_call_ends(api):
    """The demo moment: the case pops into the console on the resident's yes, mid-call, and
    must already show the repeat-offender building and its priority."""
    import asyncio
    from app import webhooks
    case = case_with_conversation(days_without_heat=1, indoor_f=64)
    asyncio.run(webhooks.enrich(case["id"]))
    got = hcdb.get_case(case["id"])
    assert got["building"]["repeat_offender"] and got["priority"] == "high"
    assert got["suggested_action"] == "schedule_inspection"
    assert got["status"] == "submitted"  # enrich never moves the case; post-call does


def test_callback_post_call_event_is_marked_ended(api, monkeypatch):
    """/callback already logs a callback_call at dial time; the dashboard tells the two apart
    by stage."""
    monkeypatch.setenv("ELEVENLABS_CALLBACK_AGENT_ID", "agent-callback")
    case = case_with_conversation(status="callback_requested")
    api.post("/api/webhooks/elevenlabs_post_call", json=post_call(agent_id="agent-callback"))
    [ended] = [e for e in hcdb.list_events(case["id"]) if e["type"] == "callback_call"]
    assert ended["payload"]["stage"] == "ended"


def test_submit_reaches_the_console_with_building_and_priority(api, monkeypatch):
    """End to end through hc.py: the resident's yes alone must leave a complete queue row."""
    from app import hc

    async def no_geocode(address):
        return {}

    async def no_sms(case, kind, text_en):
        pass

    monkeypatch.delenv("HC_TOOL_SECRET", raising=False)
    monkeypatch.setattr(hc, "geocode", no_geocode)
    monkeypatch.setattr(hc, "send_sms", no_sms)
    out = api.post("/api/tools/create_case", json={
        "resident_name": "Ramesh", "resident_phone": "5551234567", "language": "hi",
        "address": "2800 Creston Avenue, Bronx", "unit": "4B", "days_without_heat": 3,
        "indoor_f": 61}).json()
    hcdb.update_case(out["case_id"], is_violation=True)  # check_violation's job, weather-dependent
    api.post("/api/tools/submit_case", json={"case_id": out["case_id"], "resident_confirmed": True})
    got = hcdb.get_case(out["case_id"])
    assert (got["status"], got["priority"], got["building"]["source"]) == ("submitted", "urgent", "seed")


def test_seed_is_idempotent_and_backdated(api, monkeypatch):
    """Re-running the seed before the demo must not double the queue, and seeds must sort
    below the live call (older created_at)."""
    import asyncio
    from scripts import hc_seed

    async def offline(address, borough=None):
        return building.not_found(address) if "Creston" not in address else \
            await real(address, borough)

    real = building.building_lookup
    monkeypatch.setattr(hc_seed.building, "building_lookup", offline)
    asyncio.run(hc_seed.main())
    asyncio.run(hc_seed.main())
    numbers = [c["case_number"] for c in hcdb.list_cases()]
    assert sorted(numbers) == sorted(s["case_number"] for s in hc_seed.SEEDS)
    creston = hcdb.get_case_by_number("HC-104233")
    assert creston["building"]["repeat_offender"] and creston["created_at"] < hcdb.now().isoformat()
    assert [e["type"] for e in hcdb.list_events(creston["id"])][-1] == "resident_response"


def test_without_an_llm_the_agents_english_transcript_is_used(api):
    """The user's call: no Grok/Gemini key; ElevenLabs' data collection supplies the English."""
    case = case_with_conversation(language="hi")
    body = post_call(analysis={"transcript_summary": "No heat for three days.",
                               "data_collection_results": {"transcript_en": {"value": "Resident: No heat."}}})
    api.post("/api/webhooks/elevenlabs_post_call", json=body)
    got = hcdb.get_case(case["id"])
    assert (got["transcript_en"], got["summary_en"]) == ("Resident: No heat.", "No heat for three days.")


def test_callback_without_an_llm_uses_the_agents_english(api, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_CALLBACK_AGENT_ID", "agent-callback")
    case = case_with_conversation(status="callback_requested", language="hi")
    body = post_call(agent_id="agent-callback",
                     analysis={"data_collection_results": {"transcript_en": {"value": "Resident: Yes."}}})
    api.post("/api/webhooks/elevenlabs_post_call", json=body)
    [ended] = [e for e in hcdb.list_events(case["id"]) if e["type"] == "callback_call"]
    assert ended["text_en"] == "Resident: Yes."


def test_whatsapp_alias_routes_both_ways(api, monkeypatch):
    """Demo: Romir calls from a US number but his WhatsApp is Indian. The receipt must reach
    the WhatsApp number, and his WhatsApp reply must land on the case filed under the caller ID."""
    import asyncio
    from app import sms
    monkeypatch.setenv("WHATSAPP_ALIASES", "5551234567=919108651858")
    monkeypatch.setenv("TWILIO_CHANNEL", "whatsapp")
    for k, v in (("TWILIO_ACCOUNT_SID", "AC1"), ("TWILIO_AUTH_TOKEN", "t"),
                 ("TWILIO_PHONE_NUMBER", "+14155238886")):
        monkeypatch.setenv(k, v)
    seen = []

    async def fake_post(self, url, **kw):
        import httpx
        seen.append(kw["data"]["To"])
        return httpx.Response(201, json={"sid": "SM1"})

    monkeypatch.setattr(sms.httpx.AsyncClient, "post", fake_post)
    status, _, _ = asyncio.run(sms.twilio_send("5551234567", "hi"))
    assert (status, seen) == ("sent", ["whatsapp:+919108651858"])
    case = case_with_conversation()
    api.post("/api/webhooks/twilio_sms", content="From=whatsapp%3A%2B919108651858&Body=ok",
             headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert hcdb.list_events(case["id"])[-1]["type"] == "sms_in"
