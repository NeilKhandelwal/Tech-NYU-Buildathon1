"""Heat Check API (app/hc.py): the two human checks, the heat verdict, and the data the
dashboard and post-call webhook depend on. SQLite stands in for Supabase."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import elevenlabs, hc, hcdb, heat


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("HC_DATABASE_URL", f"sqlite:///{tmp_path / 'hc.db'}")
    monkeypatch.delenv("HC_TOOL_SECRET", raising=False)
    hcdb.reset_engine()
    hcdb.metadata.create_all(hcdb.engine())

    async def no_geocode(address):
        return {}

    monkeypatch.setattr(hc, "geocode", no_geocode)
    sent = []

    async def fake_sms(case, kind, text_en):
        sent.append((case["case_number"], kind, text_en))

    monkeypatch.setattr(hc, "send_sms", fake_sms)
    client = TestClient(hc.app)
    client.sent = sent
    yield client
    hcdb.reset_engine()


def new_case(api, **over) -> dict:
    body = {"resident_name": "Asha Patel", "resident_phone": "+1 (555) 123-4567", "language": "hi",
            "address": "2800 Creston Ave, Bronx", "unit": "4B", "days_without_heat": 3,
            "indoor_f": 61, "conversation_id": "conv-1", **over}
    return api.post("/api/tools/create_case", json=body).json()


def test_create_case_issues_an_hc_number_and_links_the_conversation(api):
    """The post-call webhook finds the case through the conversation id on its intake event;
    without it the transcript and summary would never reach the caseworker."""
    out = new_case(api)
    assert out["case_number"].startswith("HC-") and len(out["case_number"]) == 9
    case = hcdb.get_case(out["case_id"])
    assert (case["resident_phone"], case["language"], case["language_label"], case["status"]) == \
        ("5551234567", "hi", "Hindi", "intake")
    assert hcdb.find_case_by_conversation("conv-1")["id"] == out["case_id"]
    assert hcdb.find_case_by_phone("5551234567")["id"] == out["case_id"]


def test_create_case_asks_again_instead_of_failing(api):
    out = new_case(api, resident_phone="555 12", address="")
    assert "resident_phone" in out["error"] and "address" in out["error"]
    assert hcdb.list_cases() == []


def test_check_violation_applies_the_day_rule_with_live_outdoor(api, monkeypatch):
    """The caseworker's violation card and the agent's explanation both come from this."""
    case_id = new_case(api, indoor_f=61)["case_id"]
    weekday_noon = datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)  # noon in NYC

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return weekday_noon

    async def outdoor(at):
        return 41.0

    monkeypatch.setattr(hc, "datetime", Frozen)
    monkeypatch.setattr(heat, "outdoor_temp_f", outdoor)
    out = api.post("/api/tools/check_violation", json={"case_id": case_id}).json()
    assert out["is_violation"] is True and out["required_f"] == 68
    assert out["rule_applied"] == "Day rule: outside 41F < 55F, inside 61F < 68F"
    case = hcdb.get_case(case_id)
    assert (case["is_violation"], case["outdoor_f"]) == (True, 41.0)


def test_warm_day_is_not_a_violation(api, monkeypatch):
    case_id = new_case(api, indoor_f=61)["case_id"]

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)

    async def outdoor(at):
        return 60.0

    monkeypatch.setattr(hc, "datetime", Frozen)
    monkeypatch.setattr(heat, "outdoor_temp_f", outdoor)
    out = api.post("/api/tools/check_violation", json={"case_id": case_id}).json()
    assert out["is_violation"] is False and "not below 55F" in out["rule_applied"]


def test_submit_needs_the_residents_yes(api):
    """Rule: never submit a case without the resident's spoken yes."""
    case_id = new_case(api)["case_id"]
    out = api.post("/api/tools/submit_case", json={"case_id": case_id}).json()
    assert out["ok"] is False and hcdb.get_case(case_id)["status"] == "intake"
    assert api.sent == []
    out = api.post("/api/tools/submit_case", json={"case_id": case_id, "resident_confirmed": True}).json()
    assert out["ok"] is True and hcdb.get_case(case_id)["status"] == "submitted"
    assert [kind for _, kind, _ in api.sent] == ["confirmation"]
    # a repeated yes must not text the resident twice
    api.post("/api/tools/submit_case", json={"case_id": case_id, "resident_confirmed": True})
    assert len(api.sent) == 1


def test_callback_needs_the_caseworkers_message(api, monkeypatch):
    """Rule: never call a resident back without the caseworker's approved message."""
    case_id = new_case(api)["case_id"]
    dials = []

    async def fake_dial(agent_id, to, variables, language=None):
        dials.append((agent_id, to, variables, language))
        return "conv-cb"

    monkeypatch.setenv("ELEVENLABS_CALLBACK_AGENT_ID", "agent-cb")
    monkeypatch.setattr(elevenlabs, "outbound_call", fake_dial)
    assert api.post(f"/api/cases/{case_id}/callback").status_code == 409 and dials == []
    r = api.post(f"/api/cases/{case_id}/action", json={
        "caseworker_action": "schedule_inspection",
        "caseworker_message_en": "An inspector will visit Tuesday between 9 and 12."})
    assert r.status_code == 200 and hcdb.get_case(case_id)["status"] == "callback_requested"
    out = api.post(f"/api/cases/{case_id}/callback").json()
    assert out == {"ok": True, "conversation_id": "conv-cb"}
    [(agent, to, variables, language)] = dials
    assert (agent, to, language) == ("agent-cb", "5551234567", "hi")
    assert variables["caseworker_message_en"].startswith("An inspector")
    # the callback agent's record_resident_response needs case_id, and the post-call webhook
    # finds the callback through its conversation id
    assert variables["case_id"] == case_id
    assert hcdb.find_case_by_conversation("conv-cb")["id"] == case_id


def test_callback_unconfigured_is_503_and_logs_nothing(api, monkeypatch):
    case_id = new_case(api)["case_id"]
    api.post(f"/api/cases/{case_id}/action", json={"caseworker_action": "resolved",
                                                    "caseworker_message_en": "Heat is restored."})
    monkeypatch.delenv("ELEVENLABS_CALLBACK_AGENT_ID", raising=False)
    assert api.post(f"/api/cases/{case_id}/callback").status_code == 503
    assert "callback_call" not in [e["type"] for e in hcdb.list_events(case_id)]


def test_resident_response_lands_and_sends_a_receipt(api):
    case_id = new_case(api)["case_id"]
    api.post(f"/api/cases/{case_id}/action", json={"caseworker_action": "schedule_inspection",
                                                    "caseworker_message_en": "Tuesday 9-12."})
    assert api.post("/api/tools/record_resident_response",
                    json={"case_id": case_id, "response": "maybe"}).json()["ok"] is False
    out = api.post("/api/tools/record_resident_response", json={
        "case_id": case_id, "response": "confirmed", "note_original": "हाँ, मंगलवार ठीक है",
        "note_en": "Yes, Tuesday is fine"}).json()
    assert out == {"ok": True}
    case = hcdb.get_case(case_id)
    assert (case["status"], case["resident_response"]) == ("called_back", "confirmed")
    [receipt] = [t for _, kind, t in api.sent if kind == "receipt"]
    assert "an inspection will be scheduled" in receipt and "confirmed" in receipt


def test_tool_secret_is_enforced_once_set(api, monkeypatch):
    monkeypatch.setenv("HC_TOOL_SECRET", "s3")
    body = {"resident_phone": "5551234567", "address": "1 Main St"}
    assert api.post("/api/tools/create_case", json=body).status_code == 401
    assert api.post("/api/tools/create_case", json=body, headers={"X-Tool-Secret": "s3"}).status_code == 200


def test_case_detail_includes_its_timeline(api):
    case_id = new_case(api)["case_id"]
    detail = api.get(f"/api/cases/{case_id}").json()
    assert detail["case_number"].startswith("HC-") and [e["type"] for e in detail["events"]] == ["intake_call"]
    assert api.get("/api/cases/not-a-uuid").status_code == 404


def test_texts_reach_hindi_and_spanish_speakers_without_an_llm():
    """With no LLM key the SMS would go out in English; the demo languages are pre-written."""
    hindi = {"language": "hi", "case_number": "HC-123456", "caseworker_action": "schedule_inspection",
             "resident_response": "confirmed"}
    assert "HC-123456" in hc.localized(hindi, "confirmation") and "हिंदी" in hc.localized(hindi, "confirmation") and hc.localized(hindi, "confirmation").startswith("CityLine")
    receipt = hc.localized(hindi, "receipt")
    assert "निरीक्षण" in receipt and "पुष्टि की" in receipt
    spanish = {**hindi, "language": "es"}
    assert "se programará una inspección" in hc.localized(spanish, "receipt")
    assert hc.localized({**hindi, "language": "en"}, "confirmation") is None  # sms.py sends the English
    assert hc.localized({**hindi, "language": "zh"}, "confirmation") is None  # falls back to translation


def test_localized_text_is_sent_as_is(monkeypatch):
    """A pre-written body must reach Twilio untranslated (no LLM round trip on the demo path)."""
    import asyncio
    from app import llm, sms

    sent, logged = [], []

    async def fake_twilio(to, body):
        sent.append(body)
        return "sent", "SM1", None

    async def no_translate(text, lang):
        raise AssertionError("must not translate a pre-written body")

    monkeypatch.setattr(sms, "twilio_send", fake_twilio)
    monkeypatch.setattr(llm, "translate", no_translate)
    monkeypatch.setattr(hcdb, "add_event", lambda *a, **k: logged.append(k) or {})
    case = {"id": "x", "language": "hi", "case_number": "HC-1", "resident_phone": "5551234567"}
    asyncio.run(sms.send_sms(case, "confirmation", "english", body="हिंदी टेक्स्ट"))
    assert sent == ["हिंदी टेक्स्ट"] and logged[0]["text_en"] == "english"


def test_demo_outdoor_override_is_used_and_disclosed(api, monkeypatch):
    """On a warm demo afternoon the day rule would never fire; the override makes it fire
    and says so on screen, so a simulated reading is never presented as live."""
    case_id = new_case(api, indoor_f=61)["case_id"]

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)  # 2pm in NYC

    async def live(at):
        raise AssertionError("the live lookup must not run while the override is set")

    monkeypatch.setattr(hc, "datetime", Frozen)
    monkeypatch.setattr(heat, "outdoor_temp_f", live)
    monkeypatch.setenv("HC_DEMO_OUTDOOR_F", "41")
    out = api.post("/api/tools/check_violation", json={"case_id": case_id}).json()
    assert out["is_violation"] is True and out["outdoor_f"] == 41.0
    assert out["rule_applied"].endswith("(demo: simulated cold day)")


def test_any_311_complaint_files_and_heat_check_stays_heat_only(api):
    """Widened to any 311 complaint: a pothole files with its description, an unknown type
    files as other instead of failing, and the heat rule is never applied to a non-heat case."""
    pothole = new_case(api, complaint_type="pothole", description="Deep pothole outside 2800 Creston",
                       indoor_f=None, days_without_heat=None)
    case = hcdb.get_case(pothole["case_id"])
    assert (case["complaint_type"], case["description"]) == ("pothole", "Deep pothole outside 2800 Creston")
    out = api.post("/api/tools/check_violation", json={"case_id": pothole["case_id"]}).json()
    assert out == {"error": "the heat check only applies to heat complaints"}
    weird = new_case(api, complaint_type="graffiti")
    assert hcdb.get_case(weird["case_id"])["complaint_type"] == "other"
    assert hcdb.get_case(new_case(api)["case_id"])["complaint_type"] == "heat"  # default


def test_non_heat_suggestion_is_an_inspection():
    from app import llm
    assert llm.suggest({"complaint_type": "pothole", "address": "1 Main St"}) == "schedule_inspection"
    assert llm.suggest({"complaint_type": "noise", "address": ""}) == "need_more_info"
    assert llm.suggest({"complaint_type": "heat", "address": "1 Main St", "indoor_f": None}) == "need_more_info"
