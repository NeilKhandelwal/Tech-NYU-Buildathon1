"""Lane B helpers (app/llm.py, app/sms.py, app/building.py): every one must degrade, never
break a call or a webhook, and the demo building must never wait on the network."""
import asyncio
import json
from datetime import date

import httpx
import pytest

from app import building, hcdb, llm, sms


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HC_DATABASE_URL", f"sqlite:///{tmp_path / 'hc.db'}")
    hcdb.reset_engine()
    hcdb.metadata.create_all(hcdb.engine())
    yield hcdb
    hcdb.reset_engine()


def mock_http(monkeypatch, module, handler):
    """Route `module`'s httpx.AsyncClient through a MockTransport; returns the request log."""
    seen, real = [], httpx.AsyncClient

    def client(*a, **k):
        def record(request):
            seen.append(request)
            return handler(request)
        return real(transport=httpx.MockTransport(record))

    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    return seen


def no_llm(monkeypatch):
    for k in ("XAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)


# ---- priority / suggestion: rules the caseworker can check by eye ----

def test_priority_ranks_cold_violations_first():
    """A 58°F apartment for three days must outrank a borderline one, or the queue buries
    the resident most at risk."""
    assert llm.priority({"is_violation": True, "indoor_f": 58, "days_without_heat": 1}, None) == "urgent"
    assert llm.priority({"is_violation": True, "indoor_f": 66, "days_without_heat": 3}, None) == "urgent"
    assert llm.priority({"is_violation": True, "indoor_f": 66, "days_without_heat": 1}, None) == "high"
    assert llm.priority({"is_violation": False, "indoor_f": 70}, None) == "normal"
    assert llm.priority({"is_violation": False, "indoor_f": 70}, {"repeat_offender": True}) == "high"


def test_analyze_without_any_llm_still_suggests_an_action(monkeypatch):
    no_llm(monkeypatch)
    out = asyncio.run(llm.analyze("Agent: hi", {"language": "en", "indoor_f": 55,
                                                "is_violation": True, "address": "x"}))
    assert out == {"transcript_en": "Agent: hi", "summary_en": None,
                   "suggested_action": "emergency_repair"}


def test_analyze_takes_grok_json_and_rejects_an_unknown_action(monkeypatch):
    """Only the four dashboard actions are valid; anything else falls back to the rule."""
    monkeypatch.setenv("XAI_API_KEY", "k")
    reply = {"transcript_en": "Agent: Hello", "summary_en": "Cold flat.", "suggested_action": "sue"}
    seen = mock_http(monkeypatch, llm, lambda r: httpx.Response(
        200, json={"choices": [{"message": {"content": json.dumps(reply)}}]}))
    out = asyncio.run(llm.analyze("Agent: नमस्ते", {"language": "hi", "indoor_f": 61, "address": "x"}))
    assert out == {"transcript_en": "Agent: Hello", "summary_en": "Cold flat.",
                   "suggested_action": "schedule_inspection"}
    assert seen[0].url == llm.XAI_URL and seen[0].headers["authorization"] == "Bearer k"


def test_grok_failure_is_none(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "k")
    mock_http(monkeypatch, llm, lambda r: httpx.Response(500))
    assert asyncio.run(llm.grok("hi")) is None


# ---- sms ----

def make_case(db, **over):
    return db.insert_case(**{"case_number": "HC-123456", "resident_phone": "5551234567",
                             "language": "hi", "language_label": "Hindi",
                             "address": "2800 Creston Ave", **over})


def test_sms_without_twilio_is_logged_as_skipped(db, monkeypatch):
    """The SMS panel fallback: with no Twilio the text still shows on the dashboard."""
    no_llm(monkeypatch)
    for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"):
        monkeypatch.delenv(k, raising=False)
    case = make_case(db)
    asyncio.run(sms.send_sms(case, "confirmation", "Your case HC-123456 was sent."))
    [event] = db.list_events(case["id"])
    assert (event["type"], event["payload"]["status"], event["text_en"]) == \
        ("sms_out", "skipped", "Your case HC-123456 was sent.")
    assert event["text_original"] == "Your case HC-123456 was sent."  # no LLM: English stands in


def test_sms_sends_translated_body_through_twilio(db, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "k")
    for k, v in (("TWILIO_ACCOUNT_SID", "AC1"), ("TWILIO_AUTH_TOKEN", "t"),
                 ("TWILIO_PHONE_NUMBER", "+15550000000")):
        monkeypatch.setenv(k, v)
    # llm and sms share the one httpx module, so a single transport answers both by host
    seen = mock_http(monkeypatch, sms, lambda r: httpx.Response(
        200, json={"choices": [{"message": {"content": "आपका केस भेजा गया"}}]})
        if r.url.host == "api.x.ai" else httpx.Response(201, json={"sid": "SM1"}))
    case = make_case(db)
    asyncio.run(sms.send_sms(case, "confirmation", "Your case was sent."))
    [twilio] = [r for r in seen if r.url.host == "api.twilio.com"]
    form = dict(x.split("=", 1) for x in twilio.content.decode().split("&"))
    assert form["To"] == "%2B15551234567" and "AC1" in str(twilio.url)
    [event] = db.list_events(case["id"])
    assert (event["payload"]["status"], event["payload"]["sid"], event["text_original"]) == \
        ("sent", "SM1", "आपका केस भेजा गया")


def test_whatsapp_channel_prefixes_both_ends_and_uses_the_sandbox(db, monkeypatch):
    no_llm(monkeypatch)
    for k, v in (("TWILIO_ACCOUNT_SID", "AC1"), ("TWILIO_AUTH_TOKEN", "t"),
                 ("TWILIO_PHONE_NUMBER", "+15550000000"), ("TWILIO_CHANNEL", "whatsapp")):
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TWILIO_WHATSAPP_NUMBER", raising=False)
    seen = mock_http(monkeypatch, sms, lambda r: httpx.Response(201, json={"sid": "SM1"}))
    asyncio.run(sms.send_sms(make_case(db, language="en"), "receipt", "Receipt"))
    [twilio] = seen
    form = dict(x.split("=", 1) for x in twilio.content.decode().split("&"))
    assert (form["To"], form["From"]) == ("whatsapp%3A%2B15551234567", "whatsapp%3A%2B14155238886")


def test_twilio_rejection_is_logged_not_raised(db, monkeypatch):
    """A trial account texting an unverified phone fails; the call flow must carry on."""
    no_llm(monkeypatch)
    for k, v in (("TWILIO_ACCOUNT_SID", "AC1"), ("TWILIO_AUTH_TOKEN", "t"),
                 ("TWILIO_PHONE_NUMBER", "+15550000000")):
        monkeypatch.setenv(k, v)
    mock_http(monkeypatch, sms, lambda r: httpx.Response(400, json={"code": 21608}))
    case = make_case(db, language="en")
    event = asyncio.run(sms.send_sms(case, "receipt", "Receipt"))
    assert event["payload"]["status"] == "failed" and "21608" in event["payload"]["error"]


# ---- building ----

def test_address_parsing_matches_open_data_spelling():
    assert building.parse("2800 Creston Ave., Bronx") == ("2800", "CRESTON AVENUE", "BRONX")
    assert building.parse("45-12 W 33 St") == ("45-12", "WEST 33 STREET", None)
    assert building.parse("Creston Avenue") is None


def test_last_season_is_the_previous_complete_one():
    assert building.last_season(date(2026, 10, 24)) == ("2025-10-01", "2026-06-01")
    assert building.last_season(date(2026, 3, 1)) == ("2024-10-01", "2025-06-01")


def test_demo_building_comes_from_the_seed_without_network(monkeypatch):
    seen = mock_http(monkeypatch, building, lambda r: httpx.Response(500))
    out = asyncio.run(building.building_lookup("2800 Creston Ave, Bronx"))
    assert (out["source"], out["repeat_offender"], seen) == ("seed", True, [])


def test_lookup_summarizes_open_data(monkeypatch):
    building._cache.clear()

    def handler(r):
        if "wvxf-dwi5" in str(r.url):
            return httpx.Response(200, json=[
                {"violationstatus": "Open", "class": "C", "inspectiondate": "2026-01-02T00:00:00",
                 "novdescription": "§ 27-2029 ADM CODE PROVIDE HEAT"},
                {"violationstatus": "Close", "class": "B", "novdescription": "paint"}])
        return httpx.Response(200, json=[{"count": "4"}])

    mock_http(monkeypatch, building, handler)
    out = asyncio.run(building.building_lookup("10 Main St", "Queens"))
    assert (out["found"], out["open_violations"], out["open_class_c"], out["heat_violations"],
            out["heat_complaints_last_season"], out["repeat_offender"]) == (True, 1, 1, 1, 4, False)
    assert out["recent"][0]["date"] == "2026-01-02"


def test_lookup_failure_is_not_found(monkeypatch):
    building._cache.clear()
    mock_http(monkeypatch, building, lambda r: httpx.Response(503))
    out = asyncio.run(building.building_lookup("11 Main St"))
    assert (out["found"], out["source"]) == (False, "none")
