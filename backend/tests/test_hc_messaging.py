"""Heat Check messaging (CONTRACT "## Heat Check messaging"): the caseworker's own texts, the
intake agent's mid-call photo request, inbound photos, and the WhatsApp alias a demo needs."""
import pytest
from fastapi.testclient import TestClient

from app import hc, hcdb, sms


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("HC_DATABASE_URL", f"sqlite:///{tmp_path / 'hc.db'}")
    for k in ("XAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "HC_TOOL_SECRET",
              "WHATSAPP_ALIASES"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TWILIO_CHANNEL", "whatsapp")
    hcdb.reset_engine()
    hcdb.metadata.create_all(hcdb.engine())
    sent = []

    async def fake_send(to_digits, body):
        sent.append((to_digits, body))
        return "sent", "SM1", None

    monkeypatch.setattr(sms, "twilio_send", fake_send)
    client = TestClient(hc.app)
    client.sent = sent
    yield client
    hcdb.reset_engine()


def make_case(**over):
    return hcdb.insert_case(**{"case_number": "HC-123456", "resident_phone": "9297800582",
                               "language": "en", "address": "2800 Creston Ave", **over})


def inbound(api, form: str):
    return api.post("/api/webhooks/twilio_sms", content=form,
                    headers={"Content-Type": "application/x-www-form-urlencoded"})


def test_caseworker_text_is_sent_and_logged(api):
    case = make_case()
    r = api.post(f"/api/cases/{case['id']}/sms", json={"text_en": "Is the heat back on?"})
    assert r.status_code == 201
    assert (r.json()["payload"]["kind"], r.json()["payload"]["status"]) == ("caseworker", "sent")
    assert api.sent == [("9297800582", "Is the heat back on?")]


def test_caseworker_text_refuses_an_empty_message(api):
    case = make_case()
    assert api.post(f"/api/cases/{case['id']}/sms", json={"text_en": "  "}).status_code == 422
    assert api.sent == []


def test_photo_request_uses_the_prewritten_hindi(api):
    case = make_case(language="hi")
    out = api.post("/api/tools/request_photo", json={"case_id": str(case["id"])}).json()
    assert out["ok"] and out["result"].startswith("Sent")
    [(_, body)] = api.sent
    assert "थर्मामीटर" in body and case["case_number"] in body
    [event] = hcdb.list_events(case["id"])
    assert event["payload"]["kind"] == "photo_request"


def test_photo_request_that_fails_still_lets_the_call_go_on(api, monkeypatch):
    async def refused(to_digits, body):
        return "failed", None, "63015"

    monkeypatch.setattr(sms, "twilio_send", refused)
    case = make_case()
    out = api.post("/api/tools/request_photo", json={"case_id": str(case["id"])}).json()
    assert out["ok"] and "may not arrive" in out["result"]


def test_inbound_photo_is_kept_and_moves_the_case_up(api):
    case = make_case()
    r = inbound(api, "From=whatsapp%3A%2B19297800582&Body=&NumMedia=1"
                     "&MediaUrl0=https%3A%2F%2Fapi.twilio.com%2Fmedia%2FME1"
                     "&MediaContentType0=image%2Fjpeg")
    assert r.status_code == 200
    [event] = hcdb.list_events(case["id"])
    [photo] = event["payload"]["media"]
    assert photo["url"] == f"/api/events/{event['id']}/media/0"
    assert (photo["content_type"], event["text_original"]) == ("image/jpeg", "")
    assert hcdb.get_case(case["id"])["updated_at"] >= case["updated_at"]


def test_whatsapp_alias_routes_both_ways(api, monkeypatch):
    """The demo resident calls from a US number but uses WhatsApp on a +91 one."""
    monkeypatch.setenv("WHATSAPP_ALIASES", "9297800582=919108651858")
    case = make_case()
    inbound(api, "From=whatsapp%3A%2B919108651858&Body=Thank+you")
    assert hcdb.list_events(case["id"])[-1]["text_original"] == "Thank you"
    assert sms.aliases().get("9297800582") == "919108651858"


def test_media_proxy_only_fetches_twilio(api, monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "t")
    import asyncio
    assert asyncio.run(sms.fetch_media("https://evil.example/x.jpg")) is None
    case = make_case()
    event = hcdb.add_event(case["id"], "sms_in", "resident", text_original="",
                           payload={"media": [{"twilio_url": "https://evil.example/x.jpg"}]})
    assert api.get(f"/api/events/{event['id']}/media/0").status_code == 404
    assert api.get(f"/api/events/{event['id']}/media/5").status_code == 404
