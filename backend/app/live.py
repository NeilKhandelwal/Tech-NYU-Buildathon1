"""The live call log the console shows: every call to the Heat Check agents appears while it is
happening, with each line transcribed and translated into English as it is said.

A poller lists the agents' ElevenLabs conversations every POLL_S. A new one that is initiated
or in progress gets a `call_started` event and a relay:

- ElevenLabs' monitoring socket first (monitor.open_socket / monitor.parse_event). It needs
  Enterprise or the realtime-monitoring flag (docs/DECISIONS.md), so
- when the socket never opens, the relay polls the conversation's transcript instead.

Lines are `transcript` events (actor resident | intake_agent | callback_agent). case_id stays
null until the agent's create_case names the case; hc.create_case then calls
hcdb.link_conversation to attach the rows already logged. The post-call webhook still writes
the whole transcript to the case afterwards, so a relay that fails costs liveness, not data.
Nothing here raises: a broken log must never touch a call.
"""
import asyncio
import json
import logging
import os
import time

import httpx

from app import hcdb, llm, monitor

logger = logging.getLogger("heatcheck")

API = "https://api.elevenlabs.io/v1/convai/conversations"
POLL_S = 2
LIVE = ("initiated", "in-progress")  # ElevenLabs' statuses before processing/done/failed
LOOKBACK_S = 3600  # only calls that started in the last hour count as possibly live


def agent_ids() -> dict[str, str]:
    """agent_id -> which agent, for the ones configured. Intake is ELEVENLABS_INTAKE_AGENT_ID
    (provision.py's name) or ELEVENLABS_AGENT_ID (the older backend's)."""
    ids = {}
    intake = os.environ.get("ELEVENLABS_INTAKE_AGENT_ID") or os.environ.get("ELEVENLABS_AGENT_ID")
    if intake:
        ids[intake] = "intake"
    if os.environ.get("ELEVENLABS_CALLBACK_AGENT_ID"):
        ids[os.environ["ELEVENLABS_CALLBACK_AGENT_ID"]] = "callback"
    return ids


def actor(role: str, agent: str) -> str:
    return "resident" if role == "user" else f"{agent}_agent" if agent in ("intake", "callback") else "agent"


class Call:
    """One live conversation's log: its case (once known) and its language (once known)."""

    def __init__(self, conversation_id: str, agent: str):
        self.conversation_id, self.agent = conversation_id, agent
        self.case: dict | None = None

    def case_id(self):
        # cheap enough per line for a demo; stops looking once found
        if self.case is None:
            self.case = hcdb.find_case_by_conversation(self.conversation_id)
        return self.case["id"] if self.case else None

    def log(self, type: str, **fields) -> dict:
        return hcdb.add_event(self.case_id(), type, fields.pop("actor", None),
                              payload={"conversation_id": self.conversation_id,
                                       "agent": self.agent, **fields.pop("payload", {})}, **fields)

    async def line(self, role: str, text: str) -> None:
        # stored first, translated after: the caseworker sees the words the moment they're said
        event = self.log("transcript", actor=actor(role, self.agent), text_original=text,
                         payload={"role": role})
        await self.translate(event["id"], text)

    async def translate(self, event_id, text: str) -> None:
        self.case_id()
        if self.case and llm.is_english(self.case.get("language")):
            hcdb.update_event(event_id, text_en=text)
            return
        english = await llm.translate(text, "en")
        if english:
            hcdb.update_event(event_id, text_en=english)

    async def correct(self, fix: monitor.Correction) -> bool:
        """Rewrite the agent line an interruption cut short. False if it isn't logged yet."""
        for e in reversed(hcdb.conversation_events(self.conversation_id, ("transcript",))):
            if e["actor"] != "resident" and e["text_original"] == fix.original:
                hcdb.update_event(e["id"], text_original=fix.corrected, text_en=None)
                await self.translate(e["id"], fix.corrected)
                return True
        return False


async def details(client: httpx.AsyncClient, conversation_id: str) -> dict:
    r = await client.get(f"{API}/{conversation_id}")
    r.raise_for_status()
    return r.json()


async def relay_socket(call: Call, key: str) -> bool:
    """Log the call from the monitoring socket. False if the socket never opened."""
    ws = await monitor.open_socket(call.conversation_id, key)
    if ws is None:
        return False
    early: dict[str, str] = {}  # corrections that arrived before their line (monitor.handle)
    async with ws:
        async for raw in ws:  # ElevenLabs closes the socket when the conversation ends
            try:
                item = monitor.parse_event(json.loads(raw))
            except ValueError:
                continue
            if isinstance(item, monitor.Line):
                text = early.pop(item.text, item.text) if item.role == "agent" else item.text
                await call.line(item.role, text)
            elif isinstance(item, monitor.Correction) and not await call.correct(item):
                early[item.original] = item.corrected
    return True


async def relay_polling(call: Call, client: httpx.AsyncClient) -> None:
    """Without monitoring: read the conversation's transcript as it grows, until it ends."""
    logged = 0
    while True:
        try:
            data = await details(client, call.conversation_id)
        except httpx.HTTPError:
            await asyncio.sleep(POLL_S)
            continue
        turns = [t for t in data.get("transcript") or []
                 if t.get("role") in ("user", "agent") and (t.get("message") or "").strip()]
        for t in turns[logged:]:
            await call.line(t["role"], t["message"].strip())
        logged = max(logged, len(turns))
        if data.get("status") not in LIVE:
            return
        await asyncio.sleep(POLL_S)


async def relay(call: Call, key: str) -> None:
    try:
        async with httpx.AsyncClient(headers={"xi-api-key": key}, timeout=10) as client:
            try:
                # the caller's number, for the console; inbound phone calls carry it here
                phone = ((await details(client, call.conversation_id)).get("metadata") or {}) \
                    .get("phone_call") or {}
            except httpx.HTTPError:
                phone = {}
            call.log("call_started", actor=f"{call.agent}_agent",
                     payload={"caller": hcdb.digits(phone.get("external_number")) or None})
            if not await relay_socket(call, key):
                await relay_polling(call, client)
    except Exception:
        logger.exception("live log for %s stopped", call.conversation_id)
    finally:
        try:
            call.log("call_ended", actor=f"{call.agent}_agent")
        except Exception:
            logger.exception("could not log the end of %s", call.conversation_id)


async def live_conversations(client: httpx.AsyncClient, ids: dict[str, str]) -> list[tuple[str, str]]:
    """(conversation_id, agent) for every conversation still under way."""
    since = int(time.time()) - LOOKBACK_S
    found = []
    # no agent configured: list the whole workspace rather than show nothing
    for agent_id, agent in (ids.items() or [(None, "other")]):
        params = {"page_size": 20, "call_start_after_unix": since}
        if agent_id:
            params["agent_id"] = agent_id
        r = await client.get(API, params=params)
        r.raise_for_status()
        for c in r.json().get("conversations") or []:
            if c.get("status") in LIVE:
                found.append((c["conversation_id"], ids.get(c.get("agent_id"), agent)))
    return found


_tasks: set[asyncio.Task] = set()  # a bare create_task can be garbage-collected mid-call


def already_logged(conversation_id: str) -> bool:
    # a restart mid-call must not log the call (and its replayed history) twice
    return bool(hcdb.conversation_events(conversation_id, ("call_started",)))


async def poll_forever() -> None:
    key = os.environ["ELEVENLABS_API_KEY"]
    seen: set[str] = set()
    async with httpx.AsyncClient(headers={"xi-api-key": key}, timeout=10) as client:
        while True:
            try:
                for conversation_id, agent in await live_conversations(client, agent_ids()):
                    if conversation_id in seen:
                        continue
                    seen.add(conversation_id)
                    if already_logged(conversation_id):
                        continue
                    task = asyncio.create_task(relay(Call(conversation_id, agent), key))
                    _tasks.add(task)
                    task.add_done_callback(_tasks.discard)
            except Exception as e:  # ElevenLabs down, bad key: try again next tick
                logger.warning("live call poll failed: %s", e)
            await asyncio.sleep(POLL_S)


def start() -> asyncio.Task | None:
    """Start the poller (from the app's lifespan). Off without a key, or with HC_LIVE_CALLS=0."""
    if not os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("HC_LIVE_CALLS") == "0":
        logger.info("live call log off (no ELEVENLABS_API_KEY, or HC_LIVE_CALLS=0)")
        return None
    task = asyncio.create_task(poll_forever())
    _tasks.add(task)
    return task
