"""Texts to and from the tenant over Twilio (CONTRACT "## SMS").

Twilio's REST API through httpx, not the twilio SDK: one POST to send and one HMAC to check an
inbound webhook don't justify a dependency. Every text is an `sms` row whether or not it left,
so the dashboard's SMS panel shows what the tenant would have got even with Twilio off.
"""
import base64
import hashlib
import hmac
import logging
import os
from urllib.parse import parse_qsl

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import insert, select

from app import db, missions, translate
from app.models import Sms, SmsCreate
from app.shared import broadcast, digits, now

router = APIRouter()
logger = logging.getLogger("backend")

API = "https://api.twilio.com/2010-04-01"
WEBHOOK_PATH = "/sms/webhooks/twilio"
# an empty <Response/> tells Twilio to send no auto-reply of its own
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
TIMEOUT_S = 10


class NotConfigured(Exception):
    """A TWILIO_* (or PUBLIC_BACKEND_URL) env var the request needs is unset."""


def env(name: str) -> str:
    # second copy of elevenlabs.env, on purpose: its exception is ElevenLabs' to catch
    value = os.environ.get(name)
    if not value:
        raise NotConfigured(f"{name} not set (see backend/.env.example)")
    return value


# ---- Twilio ----

def twilio_error(r: httpx.Response) -> str:
    """Twilio's own reason (e.g. 21608: unverified number on a trial account) when it gives one."""
    try:
        body = r.json()
        return f"Twilio {r.status_code} ({body.get('code')}): {body.get('message')}"
    except ValueError:
        return f"Twilio {r.status_code}"


async def deliver(phone: str, body: str) -> tuple[str, str | None, str | None]:
    """Text `body` to a 10-digit US number. (status, twilio_sid, error); never raises."""
    try:
        sid = env("TWILIO_ACCOUNT_SID")
        token = env("TWILIO_AUTH_TOKEN")
        sender = env("TWILIO_PHONE_NUMBER")
    except NotConfigured as e:
        return "not_sent", None, str(e)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.post(f"{API}/Accounts/{sid}/Messages.json", auth=(sid, token),
                                  data={"To": "+1" + phone, "From": sender, "Body": body})
    except httpx.HTTPError as e:
        logger.warning("sms to %s failed: %s", phone, e)
        return "failed", None, f"Twilio unreachable: {e}"
    if r.is_error:
        logger.warning("sms to %s refused: %s", phone, r.text)
        return "failed", None, twilio_error(r)
    return "sent", r.json().get("sid"), None


def signature(url: str, params: list[tuple[str, str]], token: str) -> str:
    """Twilio's X-Twilio-Signature: base64 HMAC-SHA1 over the URL plus every POST param,
    sorted by name, concatenated name-then-value."""
    data = url + "".join(k + v for k, v in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()


# ---- rows ----

def to_sms(row) -> Sms:
    d = dict(row._mapping)
    d.pop("city_id")
    return Sms(**{**d, "id": db.sms_id(d["id"]),
                  "mission_id": db.mission_id(d["mission_id"]) if d["mission_id"] else None})


def insert_sms(conn, **values) -> Sms:
    pk = conn.execute(insert(db.sms).values(
        city_id=db.current_city_id(), created_at=now(), **values)).inserted_primary_key[0]
    row = conn.execute(select(db.sms).where(
        db.sms.c.id == pk, db.sms.c.city_id == db.current_city_id())).fetchone()
    return to_sms(row)


def latest_mission_for(conn, phone: str):
    """The phone's newest mission in this city, or None: an inbound text names no mission."""
    return conn.execute(select(db.missions.c.id, db.missions.c.tenant_language).where(
        db.missions.c.tenant_phone == phone, db.missions.c.city_id == db.current_city_id())
        .order_by(db.missions.c.id.desc()).limit(1)).fetchone()


async def announce(sms: Sms) -> None:
    await broadcast("sms", sms.id)
    if sms.mission_id:
        await broadcast("mission", sms.mission_id)


# ---- sending: what other lanes call ----

async def send(mission_pk: int | None, phone: str, text_en: str, language: str) -> Sms:
    """Translate `text_en` into `language`, text it, and record it. Twilio failures never
    raise: they are the row's status, so the caller's flow carries on."""
    lang = missions.language(language or "en") or "en"
    translated = None if lang == "en" else await translate.translate(text_en, lang)
    # untranslated beats unsent: with translation off or failed, the English goes out as English
    body, body_en, lang = (translated, text_en, lang) if translated else (text_en, None, "en")
    status, sid, error = await deliver(digits(phone), body)
    with db.connect() as conn:
        sms = insert_sms(conn, mission_id=mission_pk, direction="out", phone=digits(phone),
                         body=body, body_en=body_en, language=lang, status=status,
                         error=error, twilio_sid=sid)
    await announce(sms)
    return sms


@router.post("/missions/{mission_id}/sms", status_code=201)
async def text_tenant(mission_id: str, body: SmsCreate) -> Sms:
    if not body.text_en.strip():
        raise HTTPException(status_code=422, detail="text_en is blank")
    pk = db.mission_pk(mission_id)
    with db.connect() as conn:
        mission = missions.fetch_mission(conn, pk)
    return await send(pk, mission.tenant_phone, body.text_en.strip(), mission.tenant_language)


@router.get("/missions/{mission_id}/sms")
def mission_sms(mission_id: str) -> list[Sms]:
    pk = db.mission_pk(mission_id)
    with db.connect() as conn:
        missions.fetch_mission(conn, pk)  # 404 for an unknown mission, not an empty list
        rows = conn.execute(select(db.sms).where(
            db.sms.c.mission_id == pk, db.sms.c.city_id == db.current_city_id())
            .order_by(db.sms.c.id))
        return [to_sms(r) for r in rows]


@router.get("/sms")
def list_sms() -> list[Sms]:
    with db.connect() as conn:
        rows = conn.execute(select(db.sms).where(db.sms.c.city_id == db.current_city_id())
                            .order_by(db.sms.c.id.desc()))
        return [to_sms(r) for r in rows]


# ---- receiving ----

def us_digits(e164: str) -> str:
    # "+15551234567" -> "5551234567", the form tenant_phone is stored in
    d = digits(e164)
    return d[1:] if len(d) == 11 and d.startswith("1") else d


@router.post(WEBHOOK_PATH)
async def inbound(request: Request) -> Response:
    raw = await request.body()  # form-encoded; parsed by hand so no python-multipart dependency
    try:
        token = env("TWILIO_AUTH_TOKEN")
        base = env("PUBLIC_BACKEND_URL")
    except NotConfigured as e:  # an unset secret must not mean an open endpoint on a public tunnel
        raise HTTPException(status_code=503, detail=str(e))
    params = parse_qsl(raw.decode(), keep_blank_values=True)
    # signed over the URL Twilio was given, which behind a tunnel is not request.url
    expected = signature(base.rstrip("/") + WEBHOOK_PATH, params, token)
    if not hmac.compare_digest(expected, request.headers.get("x-twilio-signature", "")):
        raise HTTPException(status_code=401, detail="bad X-Twilio-Signature")
    form = dict(params)
    phone, body = us_digits(form.get("From", "")), form.get("Body", "")
    with db.connect() as conn:
        mission = latest_mission_for(conn, phone)
    lang = mission.tenant_language if mission else "en"
    body_en = None if lang == "en" else await translate.translate(body, "en")
    with db.connect() as conn:
        sms = insert_sms(conn, mission_id=mission.id if mission else None, direction="in",
                         phone=phone, body=body, body_en=body_en, language=lang,
                         status="received", twilio_sid=form.get("MessageSid"))
    await announce(sms)
    return Response(EMPTY_TWIML, media_type="text/xml")
