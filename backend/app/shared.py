"""Helpers every router needs. They lived in main.py until the mission routers arrived:
a router module importing main would import itself in a circle."""
import json
import logging
import os
import re
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException, WebSocket

from app.models import ToolResult

# module-level set: single-process demo, so every client is in this one process
clients: set[WebSocket] = set()


async def broadcast(type: str, id: str) -> None:
    frame = json.dumps({"type": type, "id": id})
    for ws in list(clients):
        try:
            await ws.send_text(frame)
        except Exception:
            clients.discard(ws)  # dead socket; drop it and keep going


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


# ---- server tools (CONTRACT "## ElevenLabs voice"): shared by every router that has them ----

logger = logging.getLogger("backend")

BACKEND_DOWN = "I couldn't reach the case system right now, please try again later."


def voice_auth(secret: str) -> None:
    expected = os.environ.get("VOICE_TOOL_SECRET")
    if not expected:  # an unset secret must not mean an open endpoint on a public tunnel
        raise HTTPException(status_code=503, detail="VOICE_TOOL_SECRET not set (see backend/.env.example)")
    if not secrets.compare_digest(secret, expected):
        raise HTTPException(status_code=401, detail="bad X-Voice-Secret")


async def tool(run, call_id: str) -> ToolResult:
    """Run one tool. A missing call or case reads as an outage to the caller, not a crash."""
    try:
        return ToolResult(result=await run())
    except HTTPException:
        logger.warning("voice tool on unknown call %s", call_id)
        return ToolResult(result=BACKEND_DOWN)
