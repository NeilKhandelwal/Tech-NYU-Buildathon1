"""The NYC heat rule, outdoor temperature, heat readings and heat-check calls
(CONTRACT "### The heat rule").

The rule is NYC Housing Maintenance Code §27-2029: in heat season (Oct 1 – May 31), from
6:00am to 10:00pm the indoor temperature must be at least 68°F whenever it is below 55°F
outside, and from 10:00pm to 6:00am at least 62°F whatever the weather. Everything here is
judged in New York local time; readings are stored as UTC "…Z" strings.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import delete, insert, select, update

from app import db, elevenlabs
from app.models import Call, HeatReading, HeatReadingCreate, HeatSummary, ToolHeatReading, ToolResult
from app.shared import broadcast, now, tool, voice_auth

router = APIRouter()

NYC = ZoneInfo("America/New_York")
DAY_MIN_F = 68
NIGHT_MIN_F = 62
DAY_OUTDOOR_BELOW_F = 55  # the day minimum applies only when outdoor is strictly below this


# ---- the rule (pure; everything else is plumbing around these) ----

def local(at: datetime) -> datetime:
    if at.tzinfo is None:
        # a naive datetime has no instant; guessing a zone here would silently shift the hour
        raise ValueError("heat rule needs a timezone-aware datetime")
    return at.astimezone(NYC)


def in_season(at: datetime) -> bool:
    return local(at).month not in (6, 7, 8, 9)  # Oct 1 – May 31 inclusive


def is_night(at: datetime) -> bool:
    hour = local(at).hour
    return hour >= 22 or hour < 6  # 22:00–05:59; 06:00 and 21:59 are day


def required_f(at: datetime, outdoor_f: float | None) -> int | None:
    """The legal indoor minimum at `at`, or None when nothing is required or (by day,
    outdoor unknown) it can't be known."""
    if not in_season(at):
        return None
    if is_night(at):
        return NIGHT_MIN_F
    if outdoor_f is not None and outdoor_f < DAY_OUTDOOR_BELOW_F:
        return DAY_MIN_F
    return None


def is_violation(at: datetime, indoor_f: float, outdoor_f: float | None) -> bool | None:
    """True/False under the rule; None only by day in season with outdoor unknown, where
    the minimum is either 68°F or nothing and we can't tell which."""
    if in_season(at) and not is_night(at) and outdoor_f is None:
        return None
    required = required_f(at, outdoor_f)
    return required is not None and indoor_f < required


# ---- outdoor temperature (Open-Meteo, free, no key) ----

LAT, LON = 40.7128, -74.0060
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
# the forecast API refuses hours older than ~92 days (it said "allowed range from 2026-07-23"
# on 2026-10-24); past that, ERA5 in the archive API (5-day delay, so never the recent case)
FORECAST_PAST_DAYS = 90
TIMEOUT_S = 5  # a reading must not wait on the weather; None is an acceptable answer

_outdoor_cache: dict[datetime, float] = {}  # UTC hour -> °F; single process, like the rest


def _client() -> httpx.AsyncClient:
    # one seam for tests to swap in an httpx.MockTransport
    return httpx.AsyncClient(timeout=TIMEOUT_S)


async def outdoor_temp_f(at: datetime) -> float | None:
    """NYC outdoor temperature in °F for the UTC hour containing `at`; None on any failure."""
    try:
        hour = at.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        if hour in _outdoor_cache:
            return _outdoor_cache[hour]
        recent = datetime.now(timezone.utc) - hour <= timedelta(days=FORECAST_PAST_DAYS)
        day = hour.strftime("%Y-%m-%d")
        params = {"latitude": LAT, "longitude": LON, "hourly": "temperature_2m",
                  "temperature_unit": "fahrenheit", "timezone": "GMT",  # times come back in UTC
                  "start_date": day, "end_date": day}
        async with _client() as client:
            r = await client.get(FORECAST_URL if recent else ARCHIVE_URL, params=params)
            r.raise_for_status()
            hourly = r.json()["hourly"]
        value = hourly["temperature_2m"][hourly["time"].index(hour.strftime("%Y-%m-%dT%H:00"))]
        if value is None:
            return None  # not computed yet; don't cache, a later lookup may have it
        _outdoor_cache[hour] = float(value)
        return float(value)
    except Exception:
        return None  # contract: a weather failure is a null outdoor, never an error


# ---- readings ----

def parse_taken_at(value: str | None) -> datetime:
    """The instant a reading was taken. A naive time is New York wall-clock time: that is
    what a person typing "7pm" means."""
    if value is None:
        return datetime.now(timezone.utc)
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="taken_at must be an ISO 8601 timestamp")
    return at.replace(tzinfo=NYC) if at.tzinfo is None else at


def utc_z(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_reading(row) -> HeatReading:
    at = datetime.fromisoformat(row.taken_at)
    return HeatReading(id=row.id, mission_id=db.mission_id(row.mission_id), taken_at=row.taken_at,
                       indoor_f=row.indoor_f, outdoor_f=row.outdoor_f, source=row.source,
                       required_f=required_f(at, row.outdoor_f),
                       violation=is_violation(at, row.indoor_f, row.outdoor_f))


def summary(conn, mission_pk: int | None) -> HeatSummary:
    rows = conn.execute(select(db.heat_readings).where(db.heat_readings.c.mission_id == mission_pk)
                        .order_by(db.heat_readings.c.taken_at, db.heat_readings.c.id))
    readings = [to_reading(r) for r in rows]
    violations = [r for r in readings if r.violation]
    # max keeps the first of equal shortfalls, i.e. the earliest
    worst = max(violations, key=lambda r: r.required_f - r.indoor_f, default=None)
    return HeatSummary(readings=readings, violations=len(violations), worst=worst)


def deg(f: float) -> str:
    return f"{f:.1f}".removesuffix(".0") + "°F"  # "58°F", "58.5°F": what a person would say


def spoken_time(at: datetime) -> str:
    t = local(at)
    clock = t.strftime("%I").lstrip("0") + (f":{t:%M}" if t.minute else "") + t.strftime("%p").lower()
    return f"{clock} on {t:%A}, {t:%B} {t.day}"


def reading_sentence(r: HeatReading) -> str:
    at = datetime.fromisoformat(r.taken_at)
    outside = f" with {deg(r.outdoor_f)} outside" if r.outdoor_f is not None else ""
    return (f"Logged {deg(r.indoor_f)} inside at {spoken_time(at)}{outside}; "
            f"the legal minimum then was {r.required_f}°F.")


def describe(summary: HeatSummary) -> str:
    n = len(summary.readings)
    if n == 0:
        return "No heat readings have been logged yet."
    if summary.worst is None:
        unknown = sum(r.violation is None for r in summary.readings)
        tail = (f" {unknown} couldn't be checked because the outdoor temperature wasn't known."
                if unknown else "")
        return f"{n} heat reading{'s' if n != 1 else ''} logged; none was below the legal minimum.{tail}"
    first = reading_sentence(summary.worst)
    if summary.violations == 1:
        return first
    return f"{first} That is the worst of {summary.violations} readings below the legal minimum."


def mission_row(conn, mission_id: str):
    row = conn.execute(select(db.missions.c.id, db.missions.c.tenant_phone,
                              db.missions.c.tenant_language, db.missions.c.tenant_name,
                              db.missions.c.landlord_name).where(
        db.missions.c.id == db.mission_pk(mission_id),
        db.missions.c.city_id == db.current_city_id())).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="mission not found")
    return row


async def record(mission_pk: int, at: datetime, indoor_f: float, outdoor_f: float | None,
                 source: str) -> HeatReading:
    if outdoor_f is None:
        outdoor_f = await outdoor_temp_f(at)  # before the transaction: never hold it over HTTP
    with db.connect() as conn:
        pk = conn.execute(insert(db.heat_readings).values(
            mission_id=mission_pk, taken_at=utc_z(at), indoor_f=indoor_f, outdoor_f=outdoor_f,
            source=source)).inserted_primary_key[0]
        row = conn.execute(select(db.heat_readings).where(db.heat_readings.c.id == pk)).fetchone()
    reading = to_reading(row)
    await broadcast("mission", reading.mission_id)
    return reading


@router.post("/missions/{mission_id}/heat-readings", status_code=201)
async def create_heat_reading(mission_id: str, body: HeatReadingCreate) -> HeatReading:
    with db.connect() as conn:
        pk = mission_row(conn, mission_id).id
    return await record(pk, parse_taken_at(body.taken_at), body.indoor_f, body.outdoor_f, body.source)


@router.get("/missions/{mission_id}/heat-readings")
def list_heat_readings(mission_id: str) -> HeatSummary:
    with db.connect() as conn:
        return summary(conn, mission_row(conn, mission_id).id)


# ---- the tenant agent's tool ----

def verdict(r: HeatReading) -> str:
    at = datetime.fromisoformat(r.taken_at)
    logged = f"Logged {deg(r.indoor_f)}."
    if not in_season(at):
        return f"{logged} It's outside heat season, October 1 to May 31, so no minimum applies."
    if r.violation is None:
        return (f"{logged} I couldn't get the outdoor temperature, so I can't tell yet whether "
                f"that's a violation; by day the minimum is {DAY_MIN_F}°F when it's below "
                f"{DAY_OUTDOOR_BELOW_F}°F outside.")
    outside = f" with {deg(r.outdoor_f)} outside" if r.outdoor_f is not None else ""
    if r.violation:
        return (f"{logged} That's a heat violation: the legal minimum right now is "
                f"{r.required_f}°F{outside}.")
    if r.required_f is None:
        return (f"{logged} It's {deg(r.outdoor_f)} outside, so the law doesn't require heat "
                "right now; that's not a violation.")
    return f"{logged} That meets the legal minimum of {r.required_f}°F right now, so it's not a violation."


@router.post("/voice/tools/log_heat_reading")
async def voice_log_heat_reading(body: ToolHeatReading, x_voice_secret: str = Header("")) -> ToolResult:
    voice_auth(x_voice_secret)

    async def run() -> str:
        with db.connect() as conn:
            row = conn.execute(select(db.calls.c.mission_id).where(
                db.calls.c.id == db.call_pk(body.call_id),
                db.calls.c.city_id == db.current_city_id())).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="call not found")  # tool() makes it a sentence
        if row.mission_id is None:
            return "This call isn't linked to a tenant's case, so I can't log a heat reading on it."
        reading = await record(row.mission_id, datetime.now(timezone.utc), body.indoor_f, None, "voice")
        return verdict(reading)

    return await tool(run, body.call_id)


# ---- the evening heat-check call ----

@router.post("/missions/{mission_id}/heat-check-call", status_code=201)
async def heat_check_call(mission_id: str) -> Call:
    with db.connect() as conn:
        mission = mission_row(conn, mission_id)
    try:
        agent_id = elevenlabs.env("ELEVENLABS_TENANT_AGENT_ID")
    except elevenlabs.NotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    # The agent needs call_id as a dynamic variable at dial time, so the row has to exist
    # first. It is deleted if the dial fails and not broadcast until it succeeds, so no
    # client ever sees, and the table never keeps, a call that wasn't placed.
    ts = now()
    with db.connect() as conn:
        call_pk = conn.execute(insert(db.calls).values(
            city_id=db.current_city_id(), started_at=ts, updated_at=ts,
            mission_id=mission.id, kind="heat_check")).inserted_primary_key[0]
    try:
        conversation_id = await elevenlabs.outbound_call(
            agent_id, mission.tenant_phone,
            # the tenant agent serves heat checks and report-backs from one prompt, and
            # ElevenLabs refuses a call missing any variable that prompt names: send them all
            {"call_id": db.call_id(call_pk), "mission_id": mission_id, "purpose": "heat_check",
             "tenant_name": mission.tenant_name, "landlord_name": mission.landlord_name,
             "outcome": "", "commitments": ""},
            language=mission.tenant_language)
    except BaseException as e:
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
    call = Call(**db.row_to_call(row))  # a heat check touches no cases, so case_ids stays []
    await broadcast("call", call.id)
    await broadcast("mission", mission_id)
    return call
