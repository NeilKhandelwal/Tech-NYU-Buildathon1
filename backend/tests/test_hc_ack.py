"""The instant reply (app/ack.py): every WhatsApp message a resident sends gets a "got it" back
in their language, a voice note quoting what was understood, and the reply shows on the case."""
import pytest
from fastapi.testclient import TestClient

from app import hc, hcdb, sms, voicenote


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("HC_DATABASE_URL", f"sqlite:///{tmp_path / 'hc.db'}")
    for k in ("XAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "WHATSAPP_ALIASES"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    hcdb.reset_engine()
    hcdb.metadata.create_all(hcdb.engine())
    sent = []

    async def fake_send(to_digits, body):
        sent.append(body)
        return "sent", "SM1", None

    monkeypatch.setattr(sms, "twilio_send", fake_send)
    client = TestClient(hc.app)
    client.sent = sent
    yield client
    hcdb.reset_engine()


def make_case(**over):
    return hcdb.insert_case(**{"case_number": "HC-222222", "resident_phone": "5551234567",
                               "language": "hi", "address": "2800 Creston Ave", **over})


def inbound(api, form):
    return api.post("/api/webhooks/twilio_sms", content="From=whatsapp%3A%2B15551234567&" + form,
                    headers={"Content-Type": "application/x-www-form-urlencoded"})


def voice(monkeypatch, text, transcribed=True):
    async def from_form(form):
        return {"text": text, "payload": {"voice_note": True, "transcribed": transcribed}}
    monkeypatch.setattr(voicenote, "from_form", from_form)


def test_a_voice_note_is_acknowledged_in_hindi_quoting_what_we_heard(api, monkeypatch):
    voice(monkeypatch, "मेरे घर में हीटिंग नहीं है")
    case = make_case()
    inbound(api, "Body=&NumMedia=1")
    [reply] = api.sent
    assert "वॉइस संदेश मिल गया" in reply and "मेरे घर में हीटिंग नहीं है" in reply
    assert case["case_number"] in reply
    types = [(e["type"], (e["payload"] or {}).get("kind")) for e in hcdb.list_events(case["id"])]
    assert types == [("sms_in", None), ("sms_out", "ack")]  # the reply is on the case too


def test_a_typed_message_gets_the_plain_ack(api):
    case = make_case(language="es")
    inbound(api, "Body=Hola")
    [reply] = api.sent
    assert reply.startswith(f"CityLine {case['case_number']}: recibimos su mensaje.")


def test_a_note_we_could_not_make_out_asks_them_to_try_again(api, monkeypatch):
    voice(monkeypatch, voicenote.UNTRANSCRIBED, transcribed=False)
    make_case(language="en")
    inbound(api, "Body=&NumMedia=1")
    [reply] = api.sent
    assert "couldn't make it out" in reply


def test_a_message_from_an_unknown_number_gets_no_reply(api):
    inbound(api, "Body=hello")
    assert api.sent == []


def test_without_twilio_nothing_is_logged(api, monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID")
    case = make_case()
    inbound(api, "Body=hello")
    assert [e["type"] for e in hcdb.list_events(case["id"])] == ["sms_in"]
