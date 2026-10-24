"""The two things the backend asks of ElevenLabs, and the one it checks: a conversation token
for the browser, and the signature on a post-call webhook. No DB in here."""
import hashlib
import hmac
import os
import time

import httpx

API = "https://api.elevenlabs.io"
# ElevenLabs' own guidance: reject a signature older than 30 minutes, so a captured
# webhook can't be replayed later
SIGNATURE_MAX_AGE_S = 30 * 60


class NotConfigured(Exception):
    """An ELEVENLABS_* env var the request needs is unset."""


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise NotConfigured(f"{name} not set (see backend/.env.example)")
    return value


async def conversation_token() -> str:
    """A short-lived WebRTC token for one conversation with our agent. Fetched server side
    so the API key never reaches the browser."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{API}/v1/convai/conversation/token",
                             params={"agent_id": env("ELEVENLABS_AGENT_ID")},
                             headers={"xi-api-key": env("ELEVENLABS_API_KEY")})
        r.raise_for_status()
        return r.json()["token"]


def signature_ok(raw_body: bytes, header: str | None, secret: str,
                 now: float | None = None) -> bool:
    """The ElevenLabs-Signature header is `t=<unix>,v0=<hex>`, where the hex is
    HMAC-SHA256(secret, f"{t}.{raw_body}")."""
    if not header:
        return False
    try:
        values = dict(part.split("=", 1) for part in header.split(","))
        ts = values["t"]
        if abs((now or time.time()) - int(ts)) > SIGNATURE_MAX_AGE_S:
            return False
    except (KeyError, ValueError):
        return False
    expected = hmac.new(secret.encode(), ts.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, values.get("v0", ""))
