"""Twilio SMS (CONTRACT "## SMS").

The rules here: a text that can't go out is still recorded (the dashboard panel is the
fallback), a Twilio failure never becomes an HTTP error, and the inbound webhook is closed to
anyone without Twilio's signature.
"""
from urllib.parse import parse_qsl, urlencode

import httpx
import pytest

from app import sms, translate

MARIA = {"tenant_name": "Maria Lopez", "tenant_phone": "(555) 123-4567", "tenant_language": "es"}
TWILIO = {"TWILIO_ACCOUNT_SID": "AC123", "TWILIO_AUTH_TOKEN": "tok",
          "TWILIO_PHONE_NUMBER": "+15550001111", "PUBLIC_BACKEND_URL": "https://tunnel.example/"}
WEBHOOK = "https://tunnel.example/sms/webhooks/twilio"
# captured at import: a second fake_twilio in one test must wrap the real client, not the first fake
REAL_CLIENT = httpx.AsyncClient


def mission(client, **body):
    r = client.post("/missions", json={**MARIA, **body})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def twilio(monkeypatch):
    for k, v in TWILIO.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def no_twilio(monkeypatch):
    for k in TWILIO:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def spanish(monkeypatch):
    """Translation stubbed: English <-> a tagged copy, so tests can see which way it went."""
    async def fake(text, target):
        return f"[{target}] {text}"
    monkeypatch.setattr(translate, "translate", fake)


def fake_twilio(monkeypatch, handler):
    """Route sms's httpx.AsyncClient through a MockTransport; returns the request log."""
    seen = []

    def client(*a, **k):
        def record(request):
            seen.append(request)
            return handler(request)
        return REAL_CLIENT(transport=httpx.MockTransport(record), timeout=k.get("timeout"))

    monkeypatch.setattr(sms.httpx, "AsyncClient", client)
    return seen


def test_unconfigured_twilio_still_records_the_text(client, no_twilio, spanish, monkeypatch):
    """No keys yet is the normal state of a fresh clone; the SMS panel must still show it."""
    seen = fake_twilio(monkeypatch, lambda r: httpx.Response(201, json={}))
    m = mission(client)
    r = client.post(f"/missions/{m['id']}/sms", json={"text_en": "Your request was received."})
    assert r.status_code == 201, r.text
    s = r.json()
    assert (s["id"], s["mission_id"], s["direction"], s["status"]) == ("SMS-1", m["id"], "out", "not_sent")
    assert "TWILIO_ACCOUNT_SID" in s["error"] and seen == []
    assert (s["body"], s["body_en"], s["language"]) == (
        "[es] Your request was received.", "Your request was received.", "es")
    assert [x["id"] for x in client.get("/sms").json()] == ["SMS-1"]
    assert [x["id"] for x in client.get(f"/missions/{m['id']}/sms").json()] == ["SMS-1"]


def test_sends_the_translated_text_through_twilio(client, twilio, spanish, monkeypatch):
    seen = fake_twilio(monkeypatch, lambda r: httpx.Response(201, json={"sid": "SM42"}))
    m = mission(client)
    s = client.post(f"/missions/{m['id']}/sms", json={"text_en": "Hello"}).json()
    assert (s["status"], s["twilio_sid"], s["error"]) == ("sent", "SM42", None)
    req = seen[0]
    assert str(req.url) == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    assert req.headers["authorization"].startswith("Basic ")
    assert dict(parse_qsl(req.read().decode())) == {
        "To": "+15551234567", "From": "+15550001111", "Body": "[es] Hello"}


def test_twilio_refusal_is_a_failed_row_not_an_error(client, twilio, spanish, monkeypatch):
    """21608 is what a trial account says for an unverified number: the staffer needs to see it."""
    fake_twilio(monkeypatch, lambda r: httpx.Response(400, json={
        "code": 21608, "message": "The number is unverified."}))
    m = mission(client)
    r = client.post(f"/missions/{m['id']}/sms", json={"text_en": "Hello"})
    assert r.status_code == 201
    assert r.json()["status"] == "failed" and "21608" in r.json()["error"]

    def timeout(request):
        raise httpx.ConnectTimeout("slow", request=request)

    fake_twilio(monkeypatch, timeout)
    s = client.post(f"/missions/{m['id']}/sms", json={"text_en": "Hello"}).json()
    assert s["status"] == "failed" and "unreachable" in s["error"]


def test_failed_translation_sends_english_and_says_so(client, no_twilio, monkeypatch):
    async def off(text, target):
        return None
    monkeypatch.setattr(translate, "translate", off)
    m = mission(client)
    s = client.post(f"/missions/{m['id']}/sms", json={"text_en": "Hello"}).json()
    assert (s["body"], s["body_en"], s["language"]) == ("Hello", None, "en")


def test_bad_requests(client, no_twilio):
    m = mission(client)
    assert client.post(f"/missions/{m['id']}/sms", json={"text_en": "  "}).status_code == 422
    assert client.post("/missions/M-999/sms", json={"text_en": "Hi"}).status_code == 404
    assert client.get("/missions/M-999/sms").status_code == 404


def test_signature_matches_twilios_published_example():
    """The worked example from Twilio's webhook-security docs and twilio-python's tests."""
    params = [("CallSid", "CA1234567890ABCDE"), ("Caller", "+14158675309"), ("Digits", "1234"),
              ("From", "+14158675309"), ("To", "+18005551212")]
    assert sms.signature("https://mycompany.com/myapp.php?foo=1&bar=2", params, "12345") == \
        "RSOYDt4T1cUTdK1PDd93/VVr8B8="


def post_inbound(client, form, token="tok"):
    raw = urlencode(form)
    sig = sms.signature(WEBHOOK, list(form.items()), token)
    return client.post("/sms/webhooks/twilio", content=raw, headers={
        "content-type": "application/x-www-form-urlencoded", "x-twilio-signature": sig})


def test_inbound_is_closed_without_config_or_signature(client, no_twilio, monkeypatch):
    form = {"From": "+15551234567", "Body": "hola", "MessageSid": "SM1"}
    assert post_inbound(client, form).status_code == 503  # unset token must not mean open
    for k, v in TWILIO.items():
        monkeypatch.setenv(k, v)
    assert post_inbound(client, form, token="wrong").status_code == 401
    assert client.get("/sms").json() == []


def test_inbound_text_lands_on_the_tenants_mission_in_english(client, twilio, spanish):
    m = mission(client)
    r = post_inbound(client, {"From": "+15551234567", "Body": "Sí, estaré en casa",
                              "MessageSid": "SM9", "To": "+15550001111"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/xml")
    assert "<Response></Response>" in r.text
    s = client.get("/sms").json()[0]
    assert (s["direction"], s["status"], s["mission_id"], s["phone"]) == ("in", "received", m["id"], "5551234567")
    assert (s["body"], s["body_en"], s["language"], s["twilio_sid"]) == (
        "Sí, estaré en casa", "[en] Sí, estaré en casa", "es", "SM9")


def test_inbound_from_an_unknown_number_is_kept_unlinked(client, twilio, spanish):
    assert post_inbound(client, {"From": "+12125550000", "Body": "hello"}).status_code == 200
    s = client.get("/sms").json()[0]
    assert (s["mission_id"], s["phone"], s["body_en"], s["language"]) == (None, "2125550000", None, "en")
