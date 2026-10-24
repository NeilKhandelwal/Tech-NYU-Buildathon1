"""WhatsApp voice notes (app/voicenote.py): a resident's spoken reply reaches the case as text,
transcribed by ElevenLabs, and a note that can't be transcribed is still visible."""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import hc, hcdb, voicenote

AUDIO = ("&NumMedia=1&MediaUrl0=https%3A%2F%2Fapi.twilio.com%2FAccounts%2FAC1%2FMedia%2FME1"
         "&MediaContentType0=audio%2Fogg")


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("HC_DATABASE_URL", f"sqlite:///{tmp_path / 'hc.db'}")
    for k in ("XAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "WHATSAPP_ALIASES"):
        monkeypatch.delenv(k, raising=False)
    for k, v in (("TWILIO_ACCOUNT_SID", "AC1"), ("TWILIO_AUTH_TOKEN", "t"),
                 ("ELEVENLABS_API_KEY", "xi")):
        monkeypatch.setenv(k, v)
    hcdb.reset_engine()
    hcdb.metadata.create_all(hcdb.engine())
    yield TestClient(hc.app)
    hcdb.reset_engine()


def mock_http(monkeypatch, handler):
    """Route voicenote's httpx through a MockTransport; returns the request log."""
    seen, real = [], httpx.AsyncClient

    def record(request):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(voicenote.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(record), **kw))
    return seen


def twilio_and(stt):
    """Twilio hands over the audio; ElevenLabs answers with `stt`."""
    def handle(request):
        if request.url.host == "api.twilio.com":
            return httpx.Response(200, content=b"OggS fake audio")
        return stt(request)
    return handle


def make_case(**over):
    return hcdb.insert_case(**{"case_number": "HC-111111", "resident_phone": "5551234567",
                               "language": "en", "address": "2800 Creston Ave", **over})


def inbound(api, extra=AUDIO, body=""):
    return api.post("/api/webhooks/twilio_sms", content=f"From=whatsapp%3A%2B15551234567&Body={body}{extra}",
                    headers={"Content-Type": "application/x-www-form-urlencoded"})


def test_voice_note_lands_on_the_case_as_text(api, monkeypatch):
    seen = mock_http(monkeypatch, twilio_and(lambda r: httpx.Response(
        200, json={"text": "The heat is still off.", "language_code": "eng"})))
    case = make_case()
    assert inbound(api).status_code == 200
    [event] = [e for e in hcdb.list_events(case["id"]) if e["type"] == "sms_in"]
    assert (event["type"], event["text_original"], event["text_en"]) == \
        ("sms_in", "The heat is still off.", "The heat is still off.")
    assert event["payload"]["voice_note"] and event["payload"]["transcribed"]
    stt = [r for r in seen if r.url.host == "api.elevenlabs.io"][0]
    assert stt.headers["xi-api-key"] == "xi" and b"scribe_v2" in stt.content


def test_a_retired_model_falls_back_to_the_next(api, monkeypatch):
    def stt(request):
        if b"scribe_v2" in request.content:
            return httpx.Response(422, json={"detail": "invalid model_id"})
        return httpx.Response(200, json={"text": "Still cold.", "language_code": "eng"})

    mock_http(monkeypatch, twilio_and(stt))
    case = make_case()
    inbound(api)
    assert [e for e in hcdb.list_events(case["id"]) if e["type"] == "sms_in"][0]["text_original"] == "Still cold."


def test_an_untranscribable_note_is_still_shown(api, monkeypatch):
    mock_http(monkeypatch, twilio_and(lambda r: httpx.Response(500)))
    case = make_case()
    inbound(api)
    [event] = [e for e in hcdb.list_events(case["id"]) if e["type"] == "sms_in"]
    assert event["text_original"] == voicenote.UNTRANSCRIBED
    assert event["payload"]["voice_note"] and not event["payload"]["transcribed"]


def test_typed_text_with_a_photo_is_untouched(api, monkeypatch):
    seen = mock_http(monkeypatch, lambda r: httpx.Response(500))
    case = make_case()
    inbound(api, extra="&NumMedia=1&MediaUrl0=https%3A%2F%2Fapi.twilio.com%2Fx"
                       "&MediaContentType0=image%2Fjpeg", body="Thanks")
    [event] = [e for e in hcdb.list_events(case["id"]) if e["type"] == "sms_in"]
    assert event["text_original"] == "Thanks" and "voice_note" not in event["payload"]
    assert seen == []  # nothing fetched or transcribed for a non-audio attachment


def test_only_twilio_urls_are_fetched():
    assert asyncio.run(voicenote.fetch_audio("https://evil.example/a.ogg")) is None
