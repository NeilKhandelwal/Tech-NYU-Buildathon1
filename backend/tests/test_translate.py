"""app/translate.py: optional, and never the reason a call or a subtitle breaks."""
import asyncio

import httpx

from app import translate


def run(text="Hello", target="es"):
    return asyncio.run(translate.translate(text, target))


def fake_post(monkeypatch, handler):
    """Route translate's httpx.AsyncClient through a MockTransport; returns the request log."""
    seen = []
    real = httpx.AsyncClient

    def client(*a, **k):
        def record(request):
            seen.append(request)
            return handler(request)
        return real(transport=httpx.MockTransport(record), timeout=k.get("timeout"))

    monkeypatch.setattr(translate.httpx, "AsyncClient", client)
    return seen


def test_no_key_means_no_request(monkeypatch):
    """Translation is off by default; a demo without a Gemini key must not be calling out."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    seen = fake_post(monkeypatch, lambda r: httpx.Response(200, json={}))
    assert run() is None and seen == []


def test_translates_with_the_configured_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    seen = fake_post(monkeypatch, lambda r: httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": " Hola "}], "role": "model"}}]}))
    assert run() == "Hola"
    req = seen[0]
    assert req.url.path == "/v1beta/models/gemini-test:generateContent"
    assert req.headers["x-goog-api-key"] == "gk"
    body = req.read().decode()
    assert "'es'" in body and "Hello" in body


def test_failures_are_none_never_raise(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    fake_post(monkeypatch, lambda r: httpx.Response(500))
    assert run() is None

    def timeout(r):
        raise httpx.ReadTimeout("slow", request=r)

    fake_post(monkeypatch, timeout)
    assert run() is None
    fake_post(monkeypatch, lambda r: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    assert run() is None


def test_empty_text_makes_no_request(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    seen = fake_post(monkeypatch, lambda r: httpx.Response(200, json={}))
    assert run("   ") is None and seen == []
