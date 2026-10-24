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
    for k in ("XAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
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


def test_pitch_case_suggests_an_inspection_not_emergency_repair(monkeypatch):
    """The demo: 61F inside, 41F out, 3 days, Hindi. The console must say 'AI suggests:
    inspection'. A live Claude run said emergency_repair, which is only for the Emergency
    Repair Program's thresholds (64F day / 58F night after the owner fails to fix)."""
    fake_claude(monkeypatch, FakeMessages(claude_reply(json.dumps(
        {"transcript_en": "x", "summary_en": "y", "suggested_action": "emergency_repair"}))))
    case = {"language": "hi", "indoor_f": 61, "outdoor_f": 41, "is_violation": True,
            "days_without_heat": 3, "address": "2800 Creston Ave"}
    assert asyncio.run(llm.analyze("Resident: ...", case))["suggested_action"] == "schedule_inspection"


def test_analyze_without_any_llm_still_suggests_an_action(monkeypatch):
    no_llm(monkeypatch)
    out = asyncio.run(llm.analyze("Agent: hi", {"language": "en", "indoor_f": 55,
                                                "is_violation": True, "address": "x"}))
    assert out == {"transcript_en": "Agent: hi", "summary_en": None,
                   "suggested_action": "emergency_repair"}


def test_analyze_takes_grok_json_but_never_its_action(monkeypatch):
    """The suggestion is the rule's, whatever the LLM says."""
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


# ---- Claude (the key the team actually has) ----

class FakeMessages:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.reply


def claude_reply(text, stop_reason="end_turn"):
    from types import SimpleNamespace as NS
    # a thinking block first: Haiku 5.5 thinks by default, so content[0] is not the answer
    return NS(stop_reason=stop_reason, stop_details=None,
              content=[NS(type="thinking", thinking=""), NS(type="text", text=text)])


def fake_claude(monkeypatch, messages):
    from types import SimpleNamespace as NS
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(llm, "claude_client", lambda: NS(messages=messages))


def test_claude_analysis_when_only_an_anthropic_key_is_set(monkeypatch):
    """The team's only working key is Anthropic's; without this path every Hindi case
    reaches the caseworker untranslated."""
    reply = {"transcript_en": "Resident: No heat.", "summary_en": "No heat."}
    messages = FakeMessages(claude_reply("```json\n" + json.dumps(reply) + "\n```"))
    fake_claude(monkeypatch, messages)
    out = asyncio.run(llm.analyze("Resident: हीटिंग नहीं है", {"language": "hi", "indoor_f": 61, "address": "x"}))
    assert out == {**reply, "suggested_action": "schedule_inspection"}
    assert messages.calls[0]["model"] == "claude-haiku-5-5"


def test_claude_translation_skips_the_thinking_block(monkeypatch):
    fake_claude(monkeypatch, FakeMessages(claude_reply("आपका केस भेजा गया")))
    assert asyncio.run(llm.translate("Your case was sent.", "hi")) == "आपका केस भेजा गया"


def test_claude_failure_or_refusal_is_none(monkeypatch):
    import anthropic
    import httpx2
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
    fake_claude(monkeypatch, FakeMessages(error=error))
    assert asyncio.run(llm.claude("hi")) is None
    fake_claude(monkeypatch, FakeMessages(claude_reply("", stop_reason="refusal")))
    assert asyncio.run(llm.claude("hi")) is None


def test_grok_wins_over_claude_when_both_keys_are_set(monkeypatch):
    messages = FakeMessages(claude_reply("from claude"))
    fake_claude(monkeypatch, messages)
    monkeypatch.setenv("XAI_API_KEY", "k")
    mock_http(monkeypatch, llm, lambda r: httpx.Response(
        200, json={"choices": [{"message": {"content": "from grok"}}]}))
    assert asyncio.run(llm.ask("hi")) == "from grok" and messages.calls == []
