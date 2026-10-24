def test_cors_origins_read_from_env(monkeypatch):
    """The dashboard is served from a different host in any real deploy; if CORS_ORIGINS
    isn't honoured the browser silently drops every response and the desk looks dead."""
    from app.main import cors_origins

    monkeypatch.setenv("CORS_ORIGINS", "https://desk.example.gov, http://127.0.0.1:3000")
    assert cors_origins() == ["https://desk.example.gov", "http://127.0.0.1:3000"]


def test_default_origin_allowed_by_app(client):
    """The wiring, not just the parser: with CORS_ORIGINS unset the local dashboard
    at http://localhost:3000 must still get the header echoed back."""
    r = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
