import json
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from livekit import api
from sqlalchemy import insert, select, update

from app import codes, db
from app.models import (Call, CallCaseLink, CallCreate, CallDetail, CallStatus, CallUpdate, Case,
                        CaseCreate, CaseCreated, CaseEvent, CaseUpdate, NoteCreate,
                        Stats, TranscriptCreate, TranscriptLine)

load_dotenv(".env")  # LIVEKIT_* for /token, CORS_ORIGINS; python-dotenv ships with uvicorn[standard]

@asynccontextmanager
async def lifespan(_app):
    db.init_db()  # alembic upgrade head: the only path that touches the schema
    yield


def cors_origins() -> list[str]:
    """Allowed browser origins, comma-separated in CORS_ORIGINS. Origins are exact:
    http://127.0.0.1:3000 is NOT http://localhost:3000 — list both if you open both."""
    raw = os.getenv("CORS_ORIGINS") or "http://localhost:3000"  # blank means unset
    return [o.strip() for o in raw.split(",") if o.strip()]


app = FastAPI(title="EffiGov cases", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),  # read once, at import
    allow_methods=["*"],
    allow_headers=["*"],
)

# module-level set: single-process demo, so every client is in this one process
clients: set[WebSocket] = set()

# same reason: one process, so this dict is the whole rate limiter. Call id -> wrong codes tried.
lookup_misses: dict[str, int] = {}


async def broadcast(type: str, id: str) -> None:
    frame = json.dumps({"type": type, "id": id})
    for ws in list(clients):
        try:
            await ws.send_text(frame)
        except Exception:
            clients.discard(ws)  # dead socket; drop it and keep going


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        while True:
            await ws.receive_text()  # clients never send; this just parks until disconnect
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


# 64 is a name, not a payload: the header is trusted (no backend auth yet), so what it
# writes into the audit log is bounded here rather than at the column.
ACTOR_MAX = 64


def clean_actor(raw: str) -> str | None:
    """The X-Actor header as it is stored: a trimmed name, or None when nobody is named."""
    return raw.strip()[:ACTOR_MAX] or None


def parse_ts(value: str | None, name: str = "since") -> str | None:
    """Validate a timestamp query parameter and hand it back as the string the columns
    are compared against.

    It is only ever compared as a string, but it still has to BE a timestamp: an unparseable
    cursor used to match nothing at all, which a client reads as "nothing has changed" —
    forever, and silently.
    """
    if value is None:
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422,
                            detail=f"{name} must be an ISO-8601 timestamp, e.g. 2026-08-30T12:00:00Z")
    return value


def notes_of(conn, case_pks: list[int]) -> dict[int, str]:
    """The notes each case shows, oldest first. There is no notes column: a note is a
    case_events row with field="note", so the JSON field is joined back together here."""
    if not case_pks:
        return {}
    rows = conn.execute(
        select(db.case_events.c.case_id, db.case_events.c.new_value)
        .where(db.case_events.c.field == "note", db.case_events.c.case_id.in_(case_pks),
               db.case_events.c.city_id == db.current_city_id())
        .order_by(db.case_events.c.id)
    )
    notes: dict[int, str] = {}
    for case_pk, text in rows:
        notes[case_pk] = f"{notes[case_pk]}\n{text}" if case_pk in notes else (text or "")
    return notes


def to_cases(conn, rows) -> list[Case]:
    notes = notes_of(conn, [r.id for r in rows])  # one query for the whole list, not one per case
    return [Case(**db.row_to_case(r), notes=notes.get(r.id, "")) for r in rows]


def fetch_case(conn, case_pk: int | None) -> Case:
    # every read of a city-owned table carries this filter; the test suite fails a SELECT
    # that reaches cases, calls or case_events without one
    row = conn.execute(select(db.cases).where(
        db.cases.c.id == case_pk, db.cases.c.city_id == db.current_city_id())).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    return to_cases(conn, [row])[0]


def to_call(conn, row) -> Call:
    call_pk = row.id
    # linked_at is only second-resolution, so case id breaks the tie: within one call the
    # lower case id is the one that was opened first
    ids = conn.execute(
        select(db.call_cases.c.case_id).where(db.call_cases.c.call_id == call_pk)
        .order_by(db.call_cases.c.linked_at, db.call_cases.c.case_id)
    )
    return Call(**db.row_to_call(row), case_ids=[db.case_id(r.case_id) for r in ids])


def fetch_call(conn, call_pk: int | None) -> Call:
    row = conn.execute(select(db.calls).where(
        db.calls.c.id == call_pk, db.calls.c.city_id == db.current_city_id())).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="call not found")
    return to_call(conn, row)


def touch_call(conn, call_pk: int, **values) -> None:
    """Every write to a call bumps updated_at; GET /calls?since= is only as good as that."""
    conn.execute(update(db.calls).where(db.calls.c.id == call_pk).values(updated_at=now(), **values))


def link_case(conn, call_pk: int, case_pk: int, how: str, source: str,
              actor: str | None) -> bool:
    """Record that this call touched this case. Returns True if the link is new.

    The join table is the truth; calls.current_case_id is only the cursor. One call_linked
    event per (case, call), so a re-link or a re-sent PATCH doesn't spam the audit log.
    """
    existing = conn.execute(
        select(db.call_cases.c.call_id)
        .where(db.call_cases.c.call_id == call_pk, db.call_cases.c.case_id == case_pk)
    ).fetchone()
    if existing is not None:
        return False
    conn.execute(insert(db.call_cases).values(call_id=call_pk, case_id=case_pk, how=how,
                                              linked_at=now()))
    log_event(conn, case_pk, "call_linked", None, db.call_id(call_pk), source, actor)
    return True


def with_transcript(conn, call: Call) -> CallDetail:
    rows = conn.execute(
        select(db.transcript).where(db.transcript.c.call_id == db.call_pk(call.id))
        .order_by(db.transcript.c.id)
    )
    lines = [TranscriptLine(**{**r._mapping, "call_id": call.id}) for r in rows]
    return CallDetail(**call.model_dump(), transcript=lines)


def log_event(conn, case_pk: int, field: str, old: str | None, new: str | None,
              source: str, actor: str | None) -> CaseEvent:
    """The only writer of case_events. source says which system, actor says which person."""
    ts = now()
    cur = conn.execute(insert(db.case_events).values(
        city_id=db.current_city_id(), case_id=case_pk, field=field, old_value=old, new_value=new, source=source, actor=actor,
        ts=ts))
    return CaseEvent(id=cur.inserted_primary_key[0], case_id=db.case_id(case_pk), field=field,
                     old_value=old, new_value=new, source=source, actor=actor, ts=ts)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/token")
def token(identity: str) -> dict:
    """Browser client joins a fresh room; the dev-mode agent is auto-dispatched into it."""
    url = os.environ.get("LIVEKIT_URL")
    key = os.environ.get("LIVEKIT_API_KEY")
    secret = os.environ.get("LIVEKIT_API_SECRET")
    if not (url and key and secret):
        raise HTTPException(status_code=500,
                            detail="LIVEKIT_* env vars not set (see backend/.env.example)")
    room = "call-" + secrets.token_hex(4)
    jwt = (
        api.AccessToken(key, secret)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .to_jwt()
    )
    return {"token": jwt, "url": url, "room": room}


# write endpoints are async so they can await broadcast() after the commit (the `with` exit)
@app.post("/cases", status_code=201)
async def create_case(body: CaseCreate, x_source: str = Header("staff"),
                      x_actor: str = Header("")) -> CaseCreated:
    ts = now()
    with db.connect() as conn:
        code = codes.new_code(conn)
        cur = conn.execute(insert(db.cases).values(
            city_id=db.current_city_id(), name=body.name, phone=digits(body.phone), issue_type=body.issue_type,
            description=body.description, lookup_code=code, created_at=ts, updated_at=ts))
        case = fetch_case(conn, cur.inserted_primary_key[0])
        log_event(conn, db.case_pk(case.id), "created", None, case.id, x_source,
                  clean_actor(x_actor))
    await broadcast("case", case.id)
    return CaseCreated(**case.model_dump(), lookup_code=code)


@app.get("/cases")
def list_cases(phone: str | None = None, since: str | None = None) -> list[Case]:
    q = select(db.cases).where(db.cases.c.city_id == db.current_city_id())
    if phone is not None:
        q = q.where(db.cases.c.phone == digits(phone))
    since = parse_ts(since)
    if since is not None:
        # inclusive, and ISO-8601 Z strings compare as strings. Timestamps are only
        # second-resolution: a strict > drops every row written in the cursor's own second,
        # permanently. A duplicate row costs a client nothing — it refetches on WS frames anyway.
        q = q.where(db.cases.c.updated_at >= since)
    with db.connect() as conn:
        return to_cases(conn, conn.execute(q.order_by(db.cases.c.id.desc())).fetchall())


# declared before /cases/{case_id}, or FastAPI reads "lookup" as a case id
@app.get("/cases/lookup")
async def lookup_by_code(code: str, x_call_id: str = Header(""), x_source: str = Header("staff"),
                         x_actor: str = Header("")) -> Case:
    """The caller's three spoken words. Unknown and malformed codes get the same 404: no oracle."""
    with db.connect() as conn:
        row = conn.execute(
            select(db.cases).where(db.cases.c.lookup_code == codes.normalize(code),
                                   db.cases.c.city_id == db.current_city_id())
        ).fetchone()
        if row is None:
            if x_call_id:
                lookup_misses[x_call_id] = lookup_misses.get(x_call_id, 0) + 1
                if lookup_misses[x_call_id] >= 5:
                    raise HTTPException(status_code=429, detail="too many attempts")
            raise HTTPException(status_code=404, detail="no case for that code")
        case = to_cases(conn, [row])[0]  # Case has no lookup_code field, so it can't leak
        log_event(conn, row.id, "looked_up", None, x_call_id or "voice", x_source,
                  clean_actor(x_actor))
    await broadcast("case", case.id)
    return case


@app.get("/cases/{case_id}")
def get_case(case_id: str) -> Case:
    with db.connect() as conn:
        return fetch_case(conn, db.case_pk(case_id))


@app.patch("/cases/{case_id}")
async def update_case(case_id: str, body: CaseUpdate, x_source: str = Header("staff"),
                      x_actor: str = Header("")) -> Case:
    case_pk = db.case_pk(case_id)
    changes = body.model_dump(exclude_none=True)
    actor = clean_actor(x_actor)
    with db.connect() as conn:
        before = fetch_case(conn, case_pk)  # 404 before touching anything
        for field, new in changes.items():
            old = getattr(before, field)
            if old != new:  # re-sending the same value is not a change worth auditing
                log_event(conn, case_pk, field, old, new, x_source, actor)
        if changes:
            conn.execute(update(db.cases).where(db.cases.c.id == case_pk)
                         .values(**changes, updated_at=now()))
        case = fetch_case(conn, case_pk)
    await broadcast("case", case.id)
    return case


@app.post("/cases/{case_id}/notes", status_code=201)
async def add_case_note(case_id: str, body: NoteCreate, x_source: str = Header("staff"),
                        x_actor: str = Header("")) -> CaseEvent:
    """One POST appends one note. No read-modify-write, so two writers can't lose a note."""
    case_pk = db.case_pk(case_id)
    with db.connect() as conn:
        case = fetch_case(conn, case_pk)  # 404 before touching anything
        event = log_event(conn, case_pk, "note", None, body.text, x_source, clean_actor(x_actor))
        # the note is the case's newest change, and ?since= has to see it
        conn.execute(update(db.cases).where(db.cases.c.id == case_pk).values(updated_at=event.ts))
    await broadcast("case", case.id)
    return event


@app.get("/cases/{case_id}/events")
def list_case_events(case_id: str) -> list[CaseEvent]:
    with db.connect() as conn:
        case = fetch_case(conn, db.case_pk(case_id))
        rows = conn.execute(
            select(db.case_events).where(db.case_events.c.case_id == db.case_pk(case.id),
                                         db.case_events.c.city_id == db.current_city_id())
            .order_by(db.case_events.c.id)
        )
        return [CaseEvent(**{**r._mapping, "case_id": case.id}) for r in rows]


@app.get("/cases/{case_id}/calls")
def list_case_calls(case_id: str) -> list[CallDetail]:
    with db.connect() as conn:
        case = fetch_case(conn, db.case_pk(case_id))
        rows = conn.execute(
            select(db.calls).join(db.call_cases, db.call_cases.c.call_id == db.calls.c.id)
            .where(db.call_cases.c.case_id == db.case_pk(case.id),
                   db.calls.c.city_id == db.current_city_id())
            .order_by(db.calls.c.id)
        ).fetchall()
        return [with_transcript(conn, to_call(conn, r)) for r in rows]


@app.post("/calls", status_code=201)
async def create_call(body: CallCreate = CallCreate()) -> Call:
    ts = now()
    with db.connect() as conn:
        cur = conn.execute(insert(db.calls).values(
            city_id=db.current_city_id(), started_at=ts, updated_at=ts, room=body.room))
        call = fetch_call(conn, cur.inserted_primary_key[0])
    await broadcast("call", call.id)
    return call


@app.get("/calls")
def list_calls(status: CallStatus | None = None, room: str | None = None,
               since: str | None = None) -> list[Call]:
    q = select(db.calls).where(db.calls.c.city_id == db.current_city_id())
    for column, value in (("status", status), ("room", room)):
        if value is not None:
            q = q.where(db.calls.c[column] == value)
    since = parse_ts(since)
    if since is not None:
        q = q.where(db.calls.c.updated_at >= since)  # inclusive, as on /cases
    with db.connect() as conn:
        rows = conn.execute(q.order_by(db.calls.c.id.desc())).fetchall()
        return [to_call(conn, r) for r in rows]


@app.get("/calls/{call_id}")
def get_call(call_id: str) -> CallDetail:
    with db.connect() as conn:
        return with_transcript(conn, fetch_call(conn, db.call_pk(call_id)))


@app.patch("/calls/{call_id}")
async def update_call(call_id: str, body: CallUpdate, x_source: str = Header("staff"),
                      x_actor: str = Header("")) -> Call:
    call_pk = db.call_pk(call_id)
    changes = body.model_dump(exclude_none=True)
    with db.connect() as conn:
        call = fetch_call(conn, call_pk)  # 404 before touching anything
        if call.status == "ended" and changes.get("status") in ("active", "needs_person"):
            raise HTTPException(status_code=409, detail="call already ended")
        if "case_id" in changes:
            case = fetch_case(conn, db.case_pk(changes.pop("case_id")))  # 404 "case not found"
            case_pk = db.case_pk(case.id)
            # the old agent path: moving the cursor is also a link
            link_case(conn, call_pk, case_pk, "looked_up", x_source, clean_actor(x_actor))
            changes["current_case_id"] = case_pk
        if changes.get("status") == "ended":
            changes["ended_at"] = now()
        if changes:
            touch_call(conn, call_pk, **changes)
        call = fetch_call(conn, call_pk)
    await broadcast("call", call.id)
    return call


@app.post("/calls/{call_id}/cases")
async def add_call_case(call_id: str, body: CallCaseLink, response: Response,
                        x_source: str = Header("staff"), x_actor: str = Header("")) -> Call:
    """Link a case to a call, and make it the one the agent is working (calls.current_case_id).

    201 when the link is new, 200 when the call was already linked to that case.
    """
    call_pk = db.call_pk(call_id)
    with db.connect() as conn:
        call = fetch_call(conn, call_pk)  # 404 before touching anything
        case = fetch_case(conn, db.case_pk(body.case_id))
        case_pk = db.case_pk(case.id)
        linked = link_case(conn, call_pk, case_pk, body.how, x_source, clean_actor(x_actor))
        response.status_code = 201 if linked else 200
        touch_call(conn, call_pk, current_case_id=case_pk)
        call = fetch_call(conn, call_pk)
    await broadcast("call", call.id)
    return call


@app.post("/calls/{call_id}/transcript", status_code=201)
async def add_transcript(call_id: str, body: TranscriptCreate) -> TranscriptLine:
    call_pk = db.call_pk(call_id)
    with db.connect() as conn:
        call = fetch_call(conn, call_pk)
        cur = conn.execute(insert(db.transcript).values(
            call_id=call_pk, role=body.role, text=body.text, ts=now()))
        row = conn.execute(
            select(db.transcript).where(db.transcript.c.id == cur.inserted_primary_key[0])
        ).fetchone()
        line = TranscriptLine(**{**row._mapping, "call_id": call.id})
        touch_call(conn, call_pk)  # a new line is a change to the call, for ?since=
    await broadcast("transcript", call.id)
    return line


@app.get("/stats")
def stats(since: str | None = None, until: str | None = None) -> Stats:
    """Containment over a window of calls.started_at (CONTRACT "## Containment").

    Bucketed in Python rather than in three COUNT queries: the window is one small
    scan, and the buckets have to partition the calls exactly for the counts to add up.
    """
    q = (select(db.calls.c.status, db.calls.c.transfer_reason)
         .where(db.calls.c.city_id == db.current_city_id()))  # a city's score is its own calls
    since, until = parse_ts(since), parse_ts(until, "until")
    if since is not None:
        q = q.where(db.calls.c.started_at >= since)  # inclusive, as on the ?since= cursor
    if until is not None:
        q = q.where(db.calls.c.started_at <= until)  # and inclusive at the far end to match
    with db.connect() as conn:
        rows = conn.execute(q).fetchall()
    active = sum(r.status == "active" for r in rows)
    # transfer_reason is the marker that survives to ended; status "needs_person" does not
    contained = sum(r.status == "ended" and r.transfer_reason is None for r in rows)
    handled = len(rows) - active
    return Stats(calls=len(rows), contained=contained, needs_person=handled - contained,
                 active=active,
                 # None, not 0: an empty window has nothing to score, and 0% reads as a failure
                 containment=contained / handled if handled else None)
