"""Outbound calls to the landlord, the caller agent's tools, tenant questions, commitments,
and the report-back call (CONTRACT "## Tenant advocate"). app/monitor.py (live relay) and
app/translate.py (subtitles) belong to the same lane.

The tenant's answer reaches a waiting ask_tenant through the question row: the answer
endpoint stores it exactly as given (the contract says so, and there is no column for a
second copy), and ask_tenant, which is polling that row, translates it to English on the
way back to the agent.
"""
import asyncio
import os

import httpx
from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import delete, insert, select, update

from app import db, elevenlabs, heat, missions, monitor, translate
from app.models import (AnswerCreate, Call, Commitment, Mission, TenantQuestion, ToolAskTenant,
                        ToolCall, ToolCommitment, ToolOutcome, ToolResult)
from app.shared import broadcast, now, tool, voice_auth

router = APIRouter()

# a previous attempt that ended can be retried; a live or unconfirmed mission can't be dialled
CALLABLE = {"ready", "committed", "refused", "no_answer"}
OUTCOMES = ("committed", "refused", "no_answer")
LIVE = ("calling", "waiting_on_tenant")
ISSUE_WORDS = {"no_heat": "no heat", "no_hot_water": "no hot water", "repair": "a repair"}
DEFAULT_ASK_TIMEOUT_S = 90
POLL_S = 0.5  # how often a waiting ask_tenant rereads its question row
NO_ANSWER_YET = "The tenant hasn't answered yet; say you'll confirm with them and follow up."


# ---- rows ----

def mission_of_call(conn, call_id: str) -> tuple[int, Mission]:
    """The call's row id and its mission. 404 (spoken as an outage by `tool`) if either is missing."""
    call_pk = db.call_pk(call_id)
    mission_pk = conn.execute(select(db.calls.c.mission_id).where(
        db.calls.c.id == call_pk, db.calls.c.city_id == db.current_city_id())).scalar()
    return call_pk, missions.fetch_mission(conn, mission_pk)


def set_status(conn, mission_pk: int, status: str, **values) -> None:
    conn.execute(update(db.missions).where(db.missions.c.id == mission_pk,
                                           db.missions.c.city_id == db.current_city_id())
                 .values(status=status, updated_at=now(), **values))


def to_question(row) -> TenantQuestion:
    d = dict(row._mapping)
    return TenantQuestion(**{**d, "mission_id": db.mission_id(d["mission_id"]),
                             "call_id": db.call_id(d["call_id"]) if d["call_id"] else None})


def to_commitment(row) -> Commitment:
    d = dict(row._mapping)
    return Commitment(**{**d, "mission_id": db.mission_id(d["mission_id"]),
                         "call_id": db.call_id(d["call_id"]) if d["call_id"] else None})


def fetch_question(conn, question_pk: int):
    return conn.execute(select(db.tenant_questions)
                        .where(db.tenant_questions.c.id == question_pk)).fetchone()


def to_call(row) -> Call:
    return Call(**db.row_to_call(row))  # mission calls link no cases, so case_ids stays []


# ---- dialling ----

async def dial(mission: Mission, kind: str, agent_env: str, to_number: str,
               variables: dict, language: str | None = None) -> Call:
    """Open a call row, dial, and keep the row only if the dial succeeded.

    The agent needs {{call_id}} at dial time, so the row has to exist before the dial; it is
    deleted again on any failure and nothing is broadcast until the dial is through, so no
    one ever sees a call that never rang. Config is checked first: a 503 writes nothing."""
    try:
        agent_id = elevenlabs.env(agent_env)
        elevenlabs.env("ELEVENLABS_API_KEY")
        elevenlabs.env("ELEVENLABS_PHONE_NUMBER_ID")
    except elevenlabs.NotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    ts = now()
    with db.connect() as conn:
        call_pk = conn.execute(insert(db.calls).values(
            city_id=db.current_city_id(), started_at=ts, updated_at=ts, kind=kind,
            mission_id=db.mission_pk(mission.id))).inserted_primary_key[0]
    try:
        conversation_id = await elevenlabs.outbound_call(
            agent_id, to_number, {"call_id": db.call_id(call_pk), "mission_id": mission.id,
                                  "tenant_name": mission.tenant_name, **variables}, language)
    except BaseException as e:  # BaseException: a cancelled request must not leave the row either
        with db.connect() as conn:
            conn.execute(delete(db.calls).where(db.calls.c.id == call_pk))
        if isinstance(e, elevenlabs.NotConfigured):
            raise HTTPException(status_code=503, detail=str(e))
        if isinstance(e, httpx.HTTPError):
            raise HTTPException(status_code=502, detail=f"ElevenLabs outbound call failed: {e}")
        raise
    with db.connect() as conn:
        conn.execute(update(db.calls).where(db.calls.c.id == call_pk)
                     .values(conversation_id=conversation_id, updated_at=now()))
        row = conn.execute(select(db.calls).where(
            db.calls.c.id == call_pk, db.calls.c.city_id == db.current_city_id())).fetchone()
    return to_call(row)


@router.post("/missions/{mission_id}/call", status_code=201)
async def call_landlord(mission_id: str) -> Call:
    mission_pk = db.mission_pk(mission_id)
    with db.connect() as conn:
        mission = missions.fetch_mission(conn, mission_pk)
    if mission.status not in CALLABLE:
        raise HTTPException(status_code=409, detail=f"mission is {mission.status}; it must be ready")
    call = await dial(mission, "outbound", "ELEVENLABS_CALLER_AGENT_ID", mission.landlord_phone,
                      {"landlord_name": mission.landlord_name})
    with db.connect() as conn:
        set_status(conn, mission_pk, "calling", outcome=None)  # a retry starts with no outcome
    monitor.start(db.call_pk(call.id), call.conversation_id, mission.tenant_language)
    await broadcast("call", call.id)
    await broadcast("mission", mission.id)
    return call


def commitments_text(items: list[Commitment]) -> str:
    if not items:
        return "None."
    return " ".join(f"{c.what}{f' by {c.due}' if c.due else ''}." for c in items)


@router.post("/missions/{mission_id}/report-call", status_code=201)
async def report_call(mission_id: str) -> Call:
    with db.connect() as conn:
        mission = missions.fetch_mission(conn, db.mission_pk(mission_id))
        commitments = list_commitments_in(conn, db.mission_pk(mission.id))
    call = await dial(mission, "report", "ELEVENLABS_TENANT_AGENT_ID", mission.tenant_phone,
                      {"landlord_name": mission.landlord_name,
                       "outcome": mission.outcome or mission.status,
                       "commitments": commitments_text(commitments)},
                      language=mission.tenant_language)
    await broadcast("call", call.id)
    await broadcast("mission", mission.id)
    return call


async def on_call_ended(call_pk: int | None) -> bool:
    """The post-call webhook's hook: an outbound call that ended with no set_outcome would
    otherwise leave its mission `calling` forever. True if it moved the mission."""
    with db.connect() as conn:
        row = conn.execute(select(db.calls.c.kind, db.calls.c.mission_id).where(
            db.calls.c.id == call_pk, db.calls.c.city_id == db.current_city_id())).fetchone()
        if row is None or row.kind != "outbound" or row.mission_id is None:
            return False
        moved = conn.execute(
            update(db.missions)
            .where(db.missions.c.id == row.mission_id, db.missions.c.status.in_(LIVE),
                   db.missions.c.city_id == db.current_city_id())
            .values(status="no_answer", outcome="The call ended without an outcome.",
                    updated_at=now())).rowcount
    if moved:
        await broadcast("mission", db.mission_id(row.mission_id))
    return bool(moved)


# ---- questions and commitments ----

@router.get("/missions/{mission_id}/questions")
def list_questions(mission_id: str) -> list[TenantQuestion]:
    with db.connect() as conn:
        mission = missions.fetch_mission(conn, db.mission_pk(mission_id))
        rows = conn.execute(select(db.tenant_questions).where(
            db.tenant_questions.c.mission_id == db.mission_pk(mission.id))
            .order_by(db.tenant_questions.c.id))
        return [to_question(r) for r in rows]


@router.post("/missions/{mission_id}/questions/{question_id}/answer")
async def answer_question(mission_id: str, question_id: int, body: AnswerCreate) -> TenantQuestion:
    with db.connect() as conn:
        mission = missions.fetch_mission(conn, db.mission_pk(mission_id))
        mission_pk = db.mission_pk(mission.id)
        row = fetch_question(conn, question_id)
        if row is None or row.mission_id != mission_pk:
            raise HTTPException(status_code=404, detail="question not found")
        # conditional on pending, so an answer racing the timeout can't revive an expired question
        answered = conn.execute(
            update(db.tenant_questions)
            .where(db.tenant_questions.c.id == question_id, db.tenant_questions.c.status == "pending")
            .values(answer=body.answer, status="answered", answered_at=now())).rowcount
        if not answered:
            raise HTTPException(status_code=409, detail=f"question is {row.status}")
        question = to_question(fetch_question(conn, question_id))
    await broadcast("mission", mission.id)
    return question


def list_commitments_in(conn, mission_pk: int) -> list[Commitment]:
    rows = conn.execute(select(db.commitments).where(db.commitments.c.mission_id == mission_pk)
                        .order_by(db.commitments.c.id))
    return [to_commitment(r) for r in rows]


@router.get("/missions/{mission_id}/commitments")
def list_commitments(mission_id: str) -> list[Commitment]:
    with db.connect() as conn:
        mission = missions.fetch_mission(conn, db.mission_pk(mission_id))
        return list_commitments_in(conn, db.mission_pk(mission.id))


# ---- caller-agent server tools ----

def dossier_text(conn, mission: Mission) -> str:
    d = missions.build_dossier(conn, db.mission_pk(mission.id))
    m = d.mission
    unit = f", unit {m.unit}" if m.unit else ""
    landlord = f" The landlord is {m.landlord_name}." if m.landlord_name else ""
    deadline = f", by {m.deadline}" if m.deadline else ""
    limits = m.limits or "Agree to nothing beyond the goal without asking the tenant."
    return (f"You are calling on behalf of {m.tenant_name}, the tenant at {m.address or 'an unknown address'}"
            f"{unit}.{landlord} The issue: {ISSUE_WORDS.get(m.issue or '', 'not recorded')}. "
            f"The goal: {m.goal or 'not recorded'}{deadline}. The tenant's limits: {limits} "
            f"{heat.describe(d.heat)} Commitments so far: {commitments_text(d.commitments)}")


@router.post("/voice/tools/get_dossier")
async def tool_get_dossier(body: ToolCall, x_voice_secret: str = Header("")) -> ToolResult:
    voice_auth(x_voice_secret)

    async def run() -> str:
        with db.connect() as conn:
            _, mission = mission_of_call(conn, body.call_id)
            return dossier_text(conn, mission)

    return await tool(run, body.call_id)


def ask_timeout_s() -> float:
    try:
        return float(os.environ.get("ASK_TENANT_TIMEOUT_S") or DEFAULT_ASK_TIMEOUT_S)
    except ValueError:
        return DEFAULT_ASK_TIMEOUT_S


async def wait_for_answer(question_pk: int, timeout_s: float) -> str | None:
    """Poll the row until it is answered or the time is up; on timeout expire it, unless an
    answer landed in the same instant (the conditional update loses, and the answer wins)."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        with db.connect() as conn:
            row = fetch_question(conn, question_pk)
        if row.status == "answered":
            return row.answer
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(POLL_S)  # async sleep: the answer request runs on this same loop
    with db.connect() as conn:
        expired = conn.execute(
            update(db.tenant_questions)
            .where(db.tenant_questions.c.id == question_pk, db.tenant_questions.c.status == "pending")
            .values(status="expired")).rowcount
        row = fetch_question(conn, question_pk)
    return None if expired else row.answer


@router.post("/voice/tools/ask_tenant")
async def tool_ask_tenant(body: ToolAskTenant, x_voice_secret: str = Header("")) -> ToolResult:
    voice_auth(x_voice_secret)

    async def run() -> str:
        with db.connect() as conn:
            call_pk, mission = mission_of_call(conn, body.call_id)
        mission_pk = db.mission_pk(mission.id)
        lang = mission.tenant_language
        translated = None if lang == "en" else await translate.translate(body.question, lang)
        with db.connect() as conn:
            question_pk = conn.execute(insert(db.tenant_questions).values(
                mission_id=mission_pk, call_id=call_pk, question=body.question,
                question_translated=translated, status="pending",
                created_at=now())).inserted_primary_key[0]
            set_status(conn, mission_pk, "waiting_on_tenant")
        await broadcast("mission", mission.id)
        try:
            answer = await wait_for_answer(question_pk, ask_timeout_s())
        finally:
            with db.connect() as conn:  # only undo our own status: set_outcome may have moved on
                conn.execute(update(db.missions).where(
                    db.missions.c.id == mission_pk, db.missions.c.status == "waiting_on_tenant",
                    db.missions.c.city_id == db.current_city_id())
                    .values(status="calling", updated_at=now()))
            await broadcast("mission", mission.id)
        if answer is None:
            return NO_ANSWER_YET
        english = None if lang == "en" else await translate.translate(answer, "en")
        return f"The tenant says: {english or answer}"

    return await tool(run, body.call_id)


@router.post("/voice/tools/record_commitment")
async def tool_record_commitment(body: ToolCommitment, x_voice_secret: str = Header("")) -> ToolResult:
    voice_auth(x_voice_secret)

    async def run() -> str:
        what = body.what.strip()
        if not what:
            return "Say what the landlord committed to, in a few words."
        due = body.due or None  # the LLM sends "" for a parameter it means to leave out
        with db.connect() as conn:
            call_pk, mission = mission_of_call(conn, body.call_id)
            conn.execute(insert(db.commitments).values(
                mission_id=db.mission_pk(mission.id), call_id=call_pk, party="landlord",
                what=what, due=due, created_at=now()))
        await broadcast("mission", mission.id)
        return f"Recorded: the landlord will {what}{f' by {due}' if due else ''}."

    return await tool(run, body.call_id)


@router.post("/voice/tools/set_outcome")
async def tool_set_outcome(body: ToolOutcome, x_voice_secret: str = Header("")) -> ToolResult:
    voice_auth(x_voice_secret)

    async def run() -> str:
        if body.outcome not in OUTCOMES:
            return f"outcome must be exactly one of: {', '.join(OUTCOMES)}"
        with db.connect() as conn:
            _, mission = mission_of_call(conn, body.call_id)
            mission_pk = db.mission_pk(mission.id)
            # committed means a recorded promise (CONTRACT "### Status"); without one the
            # report-back call would have nothing to tell the tenant
            if body.outcome == "committed" and not list_commitments_in(conn, mission_pk):
                return "Record what the landlord committed to with record_commitment first."
            set_status(conn, mission_pk, body.outcome, outcome=body.note.strip() or body.outcome)
        await broadcast("mission", mission.id)
        return f"Outcome recorded: {body.outcome}."

    return await tool(run, body.call_id)
