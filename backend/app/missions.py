"""Missions, the intake agent's tools, the dossier, escalations (CONTRACT "## Tenant advocate").

A mission is one tenant's problem with one landlord. Everything here rebuilds its state from
the database per request, like the case tools in main.py: a server tool is a stateless webhook.
"""
from datetime import datetime
from typing import get_args
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import insert, select, update

from app import db, heat
from app.models import (Commitment, Dossier, Escalation, EscalationCreate, HeatReading,
                        Mission, MissionCreate, MissionFields, MissionIssue, MissionUpdate,
                        TenantQuestion, ToolCall, ToolMissionUpdate, ToolResult)
from app.shared import broadcast, digits, now, tool, voice_auth

router = APIRouter()

# second copy of the enum (models.py has the Literal); the tool needs the values as data
ISSUES = get_args(MissionIssue)
ISSUE_WORDS = {"no_heat": "no heat", "no_hot_water": "no hot water", "repair": "repairs needed"}
NYC = ZoneInfo("America/New_York")  # the heat rule and the tenant both live on NYC time


# ---- reads ----

def fetch_mission(conn, mission_pk: int | None) -> Mission:
    row = conn.execute(select(db.missions).where(
        db.missions.c.id == mission_pk, db.missions.c.city_id == db.current_city_id())).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="mission not found")
    d = dict(row._mapping)
    d.pop("city_id")
    return Mission(**{**d, "id": db.mission_id(d["id"])})


def public_call_id(pk: int | None) -> str | None:
    return db.call_id(pk) if pk is not None else None


def mission_calls(conn, mission_pk: int) -> list:
    return conn.execute(select(db.calls).where(
        db.calls.c.mission_id == mission_pk, db.calls.c.city_id == db.current_city_id())
        .order_by(db.calls.c.id)).fetchall()


def build_dossier(conn, mission_pk: int | None) -> Dossier:
    mission = fetch_mission(conn, mission_pk)  # first: it is the city check for the rest
    commitments = conn.execute(select(db.commitments).where(
        db.commitments.c.mission_id == mission_pk).order_by(db.commitments.c.id)).fetchall()
    questions = conn.execute(select(db.tenant_questions).where(
        db.tenant_questions.c.mission_id == mission_pk).order_by(db.tenant_questions.c.id)).fetchall()
    return Dossier(
        mission=mission,
        heat=heat.summary(conn, mission_pk),
        commitments=[Commitment(**{**r._mapping, "mission_id": mission.id,
                                   "call_id": public_call_id(r.call_id)}) for r in commitments],
        questions=[TenantQuestion(**{**r._mapping, "mission_id": mission.id,
                                     "call_id": public_call_id(r.call_id)}) for r in questions],
        call_ids=[db.call_id(r.id) for r in mission_calls(conn, mission_pk)],
    )


# ---- field rules, shared by the REST endpoints and the intake tools ----

def field_error(fields: dict) -> str | None:
    """Why these field values can't be stored, as a sentence; None when they can."""
    for name in ("tenant_phone", "landlord_phone"):
        if name in fields:
            n = len(digits(fields[name]))
            # landlord_phone "" means not known yet; the tenant's number is required
            if n != 10 and not (name == "landlord_phone" and n == 0 and fields[name] == ""):
                return (f"The {name.replace('_', ' ')} has {n} digits; a phone number should "
                        "have ten. Could you say it again?")
    if fields.get("issue") is not None and fields["issue"] not in ISSUES:
        return f"issue must be exactly one of: {', '.join(ISSUES)}"
    if "tenant_language" in fields and not language(fields["tenant_language"]):
        return "tenant_language must be a language code such as en or es."
    return None


def language(tag: str) -> str:
    # BCP-47 primary tag: "es-MX" and "ES_mx" are both the Spanish the agent speaks
    return tag.strip().replace("_", "-").split("-")[0].lower()


def normalise(fields: dict) -> dict:
    """The stored form: digits-only phones, a lowercased primary language tag."""
    out = dict(fields)
    for name in ("tenant_phone", "landlord_phone"):
        if name in out:
            out[name] = digits(out[name])
    if "tenant_language" in out:
        out["tenant_language"] = language(out["tenant_language"])
    return out


def missing(m: Mission) -> list[str]:
    """What the outbound call can't go without, in words the agent can say."""
    needs = [("tenant name", bool(m.tenant_name.strip())),
             ("tenant phone", len(m.tenant_phone) == 10),
             ("landlord phone", len(m.landlord_phone) == 10),
             ("issue", m.issue is not None),
             ("goal", bool(m.goal.strip()))]
    return [name for name, ok in needs if not ok]


def write_mission(conn, mission_pk: int, **values) -> Mission:
    conn.execute(update(db.missions).where(db.missions.c.id == mission_pk)
                 .values(updated_at=now(), **values))
    return fetch_mission(conn, mission_pk)


def insert_mission(conn, fields: dict) -> int:
    ts = now()
    cur = conn.execute(insert(db.missions).values(
        city_id=db.current_city_id(), created_at=ts, updated_at=ts, **fields))
    return cur.inserted_primary_key[0]


# ---- missions ----

@router.post("/missions", status_code=201)
async def create_mission(body: MissionCreate) -> Mission:
    fields = body.model_dump(exclude_none=True)  # None = let the column default ("" / "en")
    error = field_error(fields)
    if error:
        raise HTTPException(status_code=422, detail=error)
    with db.connect() as conn:
        mission = fetch_mission(conn, insert_mission(conn, normalise(fields)))
    await broadcast("mission", mission.id)
    return mission


@router.get("/missions")
def list_missions() -> list[Mission]:
    with db.connect() as conn:
        pks = conn.execute(select(db.missions.c.id).where(
            db.missions.c.city_id == db.current_city_id()).order_by(db.missions.c.id.desc())).scalars()
        return [fetch_mission(conn, pk) for pk in pks]


@router.get("/missions/{mission_id}")
def get_mission(mission_id: str) -> Mission:
    with db.connect() as conn:
        return fetch_mission(conn, db.mission_pk(mission_id))


@router.patch("/missions/{mission_id}")
async def update_mission(mission_id: str, body: MissionUpdate) -> Mission:
    fields = body.model_dump(exclude_none=True)  # NOT NULL columns: None means "leave it"
    error = field_error(fields)
    if error:
        raise HTTPException(status_code=422, detail=error)
    pk = db.mission_pk(mission_id)
    with db.connect() as conn:
        current = fetch_mission(conn, pk)
        if fields.get("status") == "ready":
            # ready is what lets the outbound call start, so it takes the same check as
            # mission_confirm, applied to the mission as it will be after this patch
            after = current.model_copy(update=normalise(fields))
            gaps = missing(after)
            if gaps:
                raise HTTPException(status_code=422, detail=f"not ready: missing {', '.join(gaps)}")
            # from calling/waiting_on_tenant, ready would let a second outbound call start
            if current.status not in ("draft", "ready"):
                raise HTTPException(status_code=409, detail=f"mission is {current.status}")
        mission = write_mission(conn, pk, **normalise(fields))
    await broadcast("mission", mission.id)
    return mission


@router.get("/missions/{mission_id}/dossier")
def get_dossier(mission_id: str) -> Dossier:
    with db.connect() as conn:
        return build_dossier(conn, db.mission_pk(mission_id))


# ---- escalations: template-filled drafts (no LLM); the facts come from the dossier ----

DISCLAIMER = ("This is a factual summary prepared for the tenant to review before filing. "
              "It is not legal advice.")


def local_time(iso: str) -> str:
    """"2026-10-20T23:00:00Z" -> "Tue Oct 20 2026, 7:00 PM" in NYC time, as a filing reads."""
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso  # an odd timestamp is still a fact; print it as stored
    if t.tzinfo is not None:
        t = t.astimezone(NYC)
    return t.strftime("%a %b %-d %Y, %-I:%M %p")


def reading_line(r: HeatReading) -> str:
    outdoor = f"{r.outdoor_f:g}°F outside" if r.outdoor_f is not None else "outdoor temperature unknown"
    if r.violation:
        law = f"legal minimum {r.required_f}°F — below the minimum"
    elif r.required_f is not None:
        law = f"legal minimum {r.required_f}°F"
    elif r.violation is None:
        law = "legal minimum unknown (no outdoor temperature)"
    else:
        law = "no minimum required at that time"
    return f"- {local_time(r.taken_at)}: {r.indoor_f:g}°F inside, {outdoor}; {law}."


def heat_section(d: Dossier) -> list[str]:
    readings = d.heat.readings
    if not readings:
        return ["No indoor temperature readings have been logged."]
    violations = [r for r in readings if r.violation]
    lines = [f"{len(readings)} indoor temperature reading(s) logged; {len(violations)} below the "
             "minimum required by NYC Housing Maintenance Code §27-2029."]
    # the violations first: they are the facts the complaint rests on
    lines += [reading_line(r) for r in violations]
    others = [r for r in readings if not r.violation]
    if others:
        lines.append("Other readings:")
        lines += [reading_line(r) for r in others]
    return lines


def contact_section(conn, d: Dossier, mission_pk: int) -> list[str]:
    calls = [c for c in mission_calls(conn, mission_pk) if c.kind == "outbound"]
    lines = []
    for c in calls:
        note = f" — {c.summary}" if c.summary else ""
        lines.append(f"- Called the landlord on {local_time(c.started_at)}{note}.")
    if not lines:
        lines.append("- No calls to the landlord are on record yet.")
    if d.mission.outcome:
        lines.append(f"Result: {d.mission.outcome}.")
    for c in d.commitments:
        due = f", due {local_time(c.due)}" if c.due else ""
        lines.append(f"- The {c.party} committed to: {c.what}{due} (recorded {local_time(c.created_at)}).")
    if not d.commitments:
        lines.append("- The landlord has made no recorded commitment.")
    return lines


def or_unknown(value: str | None) -> str:
    return value if value else "not provided"


def hpd_complaint(conn, d: Dossier, mission_pk: int) -> str:
    m = d.mission
    return "\n".join([
        "HPD complaint — heat / repair conditions (draft for 311 or HPD Online)",
        "",
        f"Tenant: {or_unknown(m.tenant_name)}, phone {or_unknown(m.tenant_phone)}",
        f"Address: {or_unknown(m.address)}, unit {or_unknown(m.unit)}",
        f"Owner / managing agent: {or_unknown(m.landlord_name)}, phone {or_unknown(m.landlord_phone)}",
        f"Condition: {ISSUE_WORDS.get(m.issue, 'not specified')}",
        "",
        "Indoor temperatures:",
        *heat_section(d),
        "",
        "Attempts to have the landlord correct it:",
        *contact_section(conn, d, mission_pk),
        "",
        DISCLAIMER,
    ])


def hp_action(conn, d: Dossier, mission_pk: int) -> str:
    m = d.mission
    return "\n".join([
        "HP Action — tenant's repair case summary for NYC Housing Court (draft)",
        "",
        f"Petitioner (tenant): {or_unknown(m.tenant_name)}, phone {or_unknown(m.tenant_phone)}",
        f"Respondent (owner / landlord): {or_unknown(m.landlord_name)}, phone {or_unknown(m.landlord_phone)}",
        f"Premises: {or_unknown(m.address)}, apartment {or_unknown(m.unit)}",
        "",
        f"Conditions needing correction: {ISSUE_WORDS.get(m.issue, 'not specified')}",
        "",
        "Evidence — indoor temperatures logged by the tenant:",
        *heat_section(d),
        "",
        "Notice to the landlord:",
        *contact_section(conn, d, mission_pk),
        "",
        f"What the tenant is asking for: an order directing the owner to correct the conditions"
        f"{': ' + m.goal if m.goal else ''}"
        f"{' by ' + m.deadline if m.deadline else ''}.",
        "",
        DISCLAIMER,
    ])


TEMPLATES = {"hpd_complaint": hpd_complaint, "hp_action": hp_action}


def to_escalation(row, mission_id: str) -> Escalation:
    return Escalation(**{**row._mapping, "mission_id": mission_id})


def fetch_escalation(conn, mission_pk: int, escalation_pk: int) -> Escalation:
    mission = fetch_mission(conn, mission_pk)  # the city check: escalations have no city_id
    if not 0 <= escalation_pk <= db.MAX_PK:  # an unbindable id is a 404, not a driver error
        raise HTTPException(status_code=404, detail="escalation not found")
    row = conn.execute(select(db.escalations).where(
        db.escalations.c.id == escalation_pk, db.escalations.c.mission_id == mission_pk)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="escalation not found")
    return to_escalation(row, mission.id)


@router.post("/missions/{mission_id}/escalations", status_code=201)
async def create_escalation(mission_id: str, body: EscalationCreate) -> Escalation:
    pk = db.mission_pk(mission_id)
    with db.connect() as conn:
        dossier = build_dossier(conn, pk)
        text = TEMPLATES[body.kind](conn, dossier, pk)
        cur = conn.execute(insert(db.escalations).values(
            mission_id=pk, kind=body.kind, body=text, created_at=now()))
        escalation = fetch_escalation(conn, pk, cur.inserted_primary_key[0])
    await broadcast("mission", escalation.mission_id)
    return escalation


@router.get("/missions/{mission_id}/escalations")
def list_escalations(mission_id: str) -> list[Escalation]:
    pk = db.mission_pk(mission_id)
    with db.connect() as conn:
        mission = fetch_mission(conn, pk)
        rows = conn.execute(select(db.escalations).where(
            db.escalations.c.mission_id == pk).order_by(db.escalations.c.id)).fetchall()
        return [to_escalation(r, mission.id) for r in rows]


@router.post("/missions/{mission_id}/escalations/{escalation_id}/approve")
async def approve_escalation(mission_id: str, escalation_id: int) -> Escalation:
    pk = db.mission_pk(mission_id)
    with db.connect() as conn:
        escalation = fetch_escalation(conn, pk, escalation_id)
        if escalation.status == "draft":  # a re-sent approve keeps the first approval time
            conn.execute(update(db.escalations).where(db.escalations.c.id == escalation_id)
                         .values(status="approved", approved_at=now()))
        write_mission(conn, pk, status="escalated")
        escalation = fetch_escalation(conn, pk, escalation_id)
    await broadcast("mission", escalation.mission_id)
    return escalation


# ---- intake agent's server tools (same rules as main.py's /voice/tools/*) ----

def intake_call(conn, call_id: str):
    """The call row, city-scoped; a 404 reads to the caller as an outage via shared.tool."""
    row = conn.execute(select(db.calls).where(
        db.calls.c.id == db.call_pk(call_id), db.calls.c.city_id == db.current_city_id())).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="call not found")
    return row


def still_needed(m: Mission) -> str:
    gaps = missing(m)
    return f" Still needed: {', '.join(gaps)}." if gaps else " Everything needed is in; read the card back and call mission_confirm."


@router.post("/voice/tools/mission_update")
async def voice_mission_update(body: ToolMissionUpdate, x_voice_secret: str = Header("")) -> ToolResult:
    voice_auth(x_voice_secret)

    async def run() -> str:
        # the LLM sends "" for a parameter it means to leave out
        fields = {k: v for k, v in body.model_dump(exclude={"call_id"}).items() if v}
        error = field_error(fields)
        if error:
            return error  # nothing written: the agent corrects and re-sends
        if not fields:
            return "Nothing to update."
        fields = normalise(fields)
        with db.connect() as conn:
            call = intake_call(conn, body.call_id)
            linked = call.mission_id is None
            if linked:
                # NOT NULL columns with no default: blank until the tenant gives them
                pk = insert_mission(conn, {"tenant_name": "", "tenant_phone": "", **fields})
                conn.execute(update(db.calls).where(db.calls.c.id == call.id)
                             .values(mission_id=pk, kind="intake", updated_at=now()))
                mission = fetch_mission(conn, pk)
            else:
                pk = call.mission_id
                current = fetch_mission(conn, pk)
                # the tenant confirmed a different card; an edit needs a fresh confirmation
                status = "draft" if current.status == "ready" else current.status
                mission = write_mission(conn, pk, status=status, **fields)
        await broadcast("mission", mission.id)
        if linked:
            await broadcast("call", body.call_id)
        verb = "Started" if linked else "Updated"
        return f"{verb} mission {mission.id}.{still_needed(mission)}"

    return await tool(run, body.call_id)


def spoken_digits(phone: str) -> str:
    # "5559876543" -> "555 987 6543": TTS reads grouped digits, not "five billion..."
    return f"{phone[:3]} {phone[3:6]} {phone[6:]}"


def card(m: Mission) -> str:
    """The confirmed mission read back as one sentence."""
    where = f" at {m.address}" + (f", unit {m.unit}" if m.unit else "") if m.address else ""
    landlord = m.landlord_name or "the landlord"
    by = f" by {m.deadline}" if m.deadline else ""
    limits = f" Limits: {m.limits}" if m.limits else " No limits were given, so I'll agree to nothing without asking you"
    return (f"Confirmed mission {m.id}: I'll call {landlord} at {spoken_digits(m.landlord_phone)} "
            f"for {m.tenant_name}{where} about {ISSUE_WORDS[m.issue]}, asking for {m.goal}{by}, "
            f"and reach you at {spoken_digits(m.tenant_phone)}.{limits.rstrip('.')}.")


@router.post("/voice/tools/mission_confirm")
async def voice_mission_confirm(body: ToolCall, x_voice_secret: str = Header("")) -> ToolResult:
    voice_auth(x_voice_secret)

    async def run() -> str:
        with db.connect() as conn:
            call = intake_call(conn, body.call_id)
            if call.mission_id is None:
                return "No mission yet; call mission_update with the tenant's details first."
            mission = fetch_mission(conn, call.mission_id)
            gaps = missing(mission)
            if gaps:
                return f"Can't confirm yet. Missing: {', '.join(gaps)}."
            if mission.status == "draft":  # past ready, the call flow owns the status
                mission = write_mission(conn, call.mission_id, status="ready")
        await broadcast("mission", mission.id)
        return card(mission)

    return await tool(run, body.call_id)
