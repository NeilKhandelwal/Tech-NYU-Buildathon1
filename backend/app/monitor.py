"""The live relay for an outbound call: ElevenLabs' monitoring WebSocket in, transcript rows
with subtitles in the tenant's language out (CONTRACT "## Tenant advocate").

    wss://api.elevenlabs.io/v1/convai/conversations/{conversation_id}/monitor

With the default `events_format=json` the socket sends `{"type": "connected"}`, the cached
history (about the last 100 events), `{"type": "history_complete"}`, then live client events
(docs: guides/realtime-monitoring, customization/events/client-events). The parser below is
a pure function over those decoded dicts so it is testable without a socket.
"""
import asyncio
import json
import logging
import os
from dataclasses import dataclass

from sqlalchemy import insert, select, update
from websockets.asyncio.client import connect

from app import db, translate
from app.shared import broadcast, now

logger = logging.getLogger("backend")

WSS = "wss://api.elevenlabs.io/v1/convai/conversations/{}/monitor"
# the socket only accepts once the conversation has started, i.e. once the landlord picks up,
# so keep knocking for about as long as a phone rings out
CONNECT_WINDOW_S = 60
CONNECT_RETRY_S = 2


@dataclass(frozen=True)
class Line:
    role: str  # "user" (on this call, the landlord) | "agent"
    text: str


@dataclass(frozen=True)
class Correction:
    original: str  # the agent line as first sent
    corrected: str  # what was actually said before the landlord interrupted


def parse_event(event: dict) -> Line | Correction | None:
    """One decoded monitor event -> a final line, a correction of one, or None (pings,
    audio, tentative transcripts, streaming parts, metadata, anything unknown)."""
    if not isinstance(event, dict):
        return None
    kind = event.get("type")
    if kind == "user_transcript":
        text = (event.get("user_transcription_event") or {}).get("user_transcript")
        return Line("user", text.strip()) if isinstance(text, str) and text.strip() else None
    if kind == "agent_response":
        text = (event.get("agent_response_event") or {}).get("agent_response")
        return Line("agent", text.strip()) if isinstance(text, str) and text.strip() else None
    if kind == "agent_response_correction":
        body = event.get("agent_response_correction_event") or {}
        original, corrected = body.get("original_agent_response"), body.get("corrected_agent_response")
        if isinstance(original, str) and isinstance(corrected, str) and corrected.strip():
            return Correction(original.strip(), corrected.strip())
        return None
    return None


async def subtitle(text: str, tenant_language: str) -> str | None:
    # both parties speak English on this call; only a non-English tenant needs subtitles
    return None if tenant_language == "en" else await translate.translate(text, tenant_language)


async def add_line(call_pk: int, role: str, text: str, tenant_language: str) -> None:
    translated = await subtitle(text, tenant_language)
    with db.connect() as conn:
        conn.execute(insert(db.transcript).values(call_id=call_pk, role=role, text=text,
                                                  translated=translated, ts=now()))
        conn.execute(update(db.calls).where(db.calls.c.id == call_pk).values(updated_at=now()))
    await broadcast("transcript", db.call_id(call_pk))


async def correct_line(call_pk: int, fix: Correction, tenant_language: str) -> bool:
    """Rewrite the agent line the correction is about, in place. False if it isn't stored yet."""
    with db.connect() as conn:
        line_pk = conn.execute(
            select(db.transcript.c.id).where(db.transcript.c.call_id == call_pk,
                                             db.transcript.c.role == "agent",
                                             db.transcript.c.text == fix.original)
            .order_by(db.transcript.c.id.desc()).limit(1)).scalar()
    if line_pk is None:
        return False
    translated = await subtitle(fix.corrected, tenant_language)
    with db.connect() as conn:
        conn.execute(update(db.transcript).where(db.transcript.c.id == line_pk)
                     .values(text=fix.corrected, translated=translated))
        conn.execute(update(db.calls).where(db.calls.c.id == call_pk).values(updated_at=now()))
    await broadcast("transcript", db.call_id(call_pk))
    return True


async def handle(call_pk: int, event: dict, tenant_language: str,
                 early: dict[str, str]) -> None:
    """Apply one event. `early` holds corrections that arrived before their line: the docs warn
    monitor events can come out of order, and a correction must never become a second line."""
    item = parse_event(event)
    if isinstance(item, Line):
        text = early.pop(item.text, item.text) if item.role == "agent" else item.text
        await add_line(call_pk, item.role, text, tenant_language)
    elif isinstance(item, Correction):
        if not await correct_line(call_pk, item, tenant_language):
            early[item.original] = item.corrected


async def open_socket(conversation_id: str, key: str):
    """Connect, retrying while the call is still ringing. None if it never opens."""
    deadline = asyncio.get_running_loop().time() + CONNECT_WINDOW_S
    while True:
        try:
            return await connect(WSS.format(conversation_id), additional_headers={"xi-api-key": key},
                                 open_timeout=10)
        except Exception as e:  # handshake refused (not started yet, no scope), DNS, timeout
            if asyncio.get_running_loop().time() >= deadline:
                logger.warning("monitor for %s never connected: %s", conversation_id, e)
                return None
            await asyncio.sleep(CONNECT_RETRY_S)


async def relay(call_pk: int, conversation_id: str, tenant_language: str) -> None:
    """Run for one call, as a background task. Never raises: a broken relay costs the
    subtitles, not the app (the post-call webhook still backfills the transcript)."""
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        return
    try:
        ws = await open_socket(conversation_id, key)
        if ws is None:
            return
        early: dict[str, str] = {}
        async with ws:
            async for raw in ws:  # ends when ElevenLabs closes the socket at conversation end
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                await handle(call_pk, event, tenant_language, early)
    except Exception:
        logger.exception("monitor relay for %s stopped", db.call_id(call_pk))


# a bare create_task can be garbage-collected mid-call; holding it here keeps it alive
_tasks: set[asyncio.Task] = set()


def start(call_pk: int, conversation_id: str, tenant_language: str) -> None:
    task = asyncio.create_task(relay(call_pk, conversation_id, tenant_language))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
