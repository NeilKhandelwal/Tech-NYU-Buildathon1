"""Heat Check API: Romir's CONTRACT.md routes (docs/final-context), served by FastAPI on Fly.

    uvicorn app.hc:app            # HC_DATABASE_URL = the Supabase Postgres connection string

The ElevenLabs agents call /api/tools/*, ElevenLabs and Twilio call /api/webhooks/* (lane B,
app/webhooks.py), and the dashboard reads Supabase directly (Realtime) and calls
/api/cases/:id/{action,callback}. Data access is app/hcdb.py; the heat rule, Open-Meteo,
ElevenLabs dialing and translation are reused from the tenant-advocate modules.
"""
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import elevenlabs, hcdb, heat

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logger = logging.getLogger("heatcheck")

app = FastAPI(title="Heat Check API")
# the dashboard is on Vercel and calls action/callback from the browser; nothing here is
# secret to read (the data is the demo's), so any origin may call
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

try:  # lane B's router; the app must still start before that file lands
    from app import webhooks
    app.include_router(webhooks.router)
except ImportError:
    logger.warning("app.webhooks not present yet; /api/webhooks/* not served")

try:
    from app import sms
except ImportError:
    sms = None

LANGUAGE_LABELS = {"en": "English", "es": "Spanish", "hi": "Hindi", "zh": "Chinese",
                   "bn": "Bengali", "ru": "Russian", "ko": "Korean", "ar": "Arabic",
                   "fr": "French", "ht": "Haitian Creole", "pl": "Polish", "ur": "Urdu"}
ACTIONS = {"schedule_inspection": "an inspection will be scheduled",
           "need_more_info": "the caseworker needs more information",
           "emergency_repair": "the case was referred for emergency repair",
           "resolved": "the case was marked resolved"}
RESPONSES = {"confirmed": "confirmed", "reschedule": "asked for another time",
             "question": "asked a question"}
FINE_RANGE = "$250-$500 per day"  # first violation (CLAUDE.md "The law"); repeats cost more


def tool_auth(x_tool_secret: str = Header("")) -> None:
    # optional so the agents work before anyone sets it; once HC_TOOL_SECRET is set on Fly,
    # every agent tool must send it as X-Tool-Secret
    expected = os.environ.get("HC_TOOL_SECRET")
    if expected and not secrets.compare_digest(x_tool_secret, expected):
        raise HTTPException(status_code=401, detail="bad X-Tool-Secret")


def fetch(case_id) -> dict:
    case = hcdb.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    return case


# The two texts residents get, pre-written for the demo languages so they arrive in the
# resident's language with no LLM. Any other language falls back to sms.py's translation.
SMS = {
    "es": {
        "confirmation": "Heat Check: su queja por falta de calefacción {case_number} fue enviada al encargado de casos de la ciudad. Le llamaremos en español.",
        "photo_request": "Heat Check {case_number}: responda a este mensaje con una foto de su termómetro o radiador. Ayuda a probar la falta de calefacción.",
        "receipt": "Heat Check {case_number}: {action}. Su respuesta: {response}. Puede responder en cualquier momento.",
        "actions": {"schedule_inspection": "se programará una inspección",
                    "need_more_info": "el encargado de casos necesita más información",
                    "emergency_repair": "el caso fue remitido para reparación de emergencia",
                    "resolved": "el caso fue marcado como resuelto"},
        "reviewed": "su caso fue revisado",
        "responses": {"confirmed": "confirmó", "reschedule": "pidió otro horario",
                      "question": "hizo una pregunta"},
    },
    "hi": {
        "confirmation": "हीट चेक: बिना हीट की आपकी शिकायत {case_number} शहर के केसवर्कर को भेज दी गई है। हम आपको हिंदी में वापस कॉल करेंगे।",
        "photo_request": "हीट चेक {case_number}: कृपया इस संदेश के जवाब में अपने थर्मामीटर या रेडिएटर की एक फ़ोटो भेजें। इससे हीट न होने का सबूत मिलता है।",
        "receipt": "हीट चेक {case_number}: {action}। आपका जवाब: {response}। आप कभी भी जवाब दे सकते हैं।",
        "actions": {"schedule_inspection": "निरीक्षण तय किया जाएगा",
                    "need_more_info": "केसवर्कर को और जानकारी चाहिए",
                    "emergency_repair": "मामला आपातकालीन मरम्मत के लिए भेजा गया",
                    "resolved": "मामला हल के रूप में दर्ज किया गया"},
        "reviewed": "आपके मामले की समीक्षा की गई",
        "responses": {"confirmed": "पुष्टि की", "reschedule": "दूसरा समय माँगा",
                      "question": "एक सवाल पूछा"},
    },
}


def localized(case: dict, kind: str) -> str | None:
    """The pre-written text for this case's language, or None (English, or no template)."""
    t = SMS.get(case["language"])
    if t is None:
        return None
    if kind in ("confirmation", "photo_request"):
        return t[kind].format(case_number=case["case_number"])
    if kind != "receipt":
        return None  # the caseworker's own words: translated by sms.py, never a template
    action = t["actions"].get(case["caseworker_action"] or "", t["reviewed"])
    response = t["responses"].get(case["resident_response"] or "", "")
    return t["receipt"].format(case_number=case["case_number"], action=action, response=response)


async def send_sms(case: dict, kind: str, text_en: str) -> dict | None:
    """The sms_out event, or None if even logging it failed."""
    if sms is None:  # lane B not merged yet: log what would have gone out
        return hcdb.add_event(case["id"], "sms_out", "system", text_en=text_en,
                              payload={"kind": kind, "status": "skipped",
                                       "reason": "sms module missing"})
    try:
        return await sms.send_sms(case, kind, text_en, body=localized(case, kind))
    except Exception:  # a failed text must never fail the tool the agent is waiting on
        logger.exception("send_sms failed")
        return None


async def geocode(address: str) -> dict:
    """lat, lon, borough from NYC Planning's GeoSearch (no key). Best effort: {} on failure."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get("https://geosearch.planninglabs.nyc/v2/search",
                                 params={"text": address, "size": 1})
            r.raise_for_status()
            feature = r.json()["features"][0]
        lon, lat = feature["geometry"]["coordinates"]
        return {"lat": lat, "lon": lon, "borough": feature["properties"].get("borough")}
    except Exception:
        return {}


# ---- intake agent tools ----

class CreateCase(BaseModel):
    resident_name: str | None = None
    resident_phone: str | None = None
    language: str | None = "en"
    language_label: str | None = None
    address: str | None = None
    unit: str | None = None
    days_without_heat: int | None = None
    indoor_f: float | None = None
    # not in the contract's list: the agent passes {{system__conversation_id}} so the post-call
    # webhook can find this case; optional so a tool config without it still works
    conversation_id: str | None = None


@app.post("/api/tools/create_case", dependencies=[Depends(tool_auth)])
async def create_case(body: CreateCase) -> dict:
    phone, address = hcdb.digits(body.resident_phone), (body.address or "").strip()
    missing = [n for n, ok in (("resident_phone", len(phone) == 10), ("address", address)) if not ok]
    if missing:
        # a 200 with a reason, not a 422: the agent can ask again instead of going silent
        return {"error": f"missing or invalid: {', '.join(missing)}; ask the resident again"}
    language = (body.language or "en").strip().lower().split("-")[0]
    case = hcdb.insert_case(
        resident_name=body.resident_name, resident_phone=phone, language=language,
        language_label=body.language_label or LANGUAGE_LABELS.get(language, language),
        address=address, unit=body.unit, days_without_heat=body.days_without_heat,
        indoor_f=body.indoor_f, **(await geocode(address)))
    hcdb.add_event(case["id"], "intake_call", "intake_agent",
                   payload={"conversation_id": body.conversation_id})
    return {"case_id": case["id"], "case_number": case["case_number"]}


class CheckViolation(BaseModel):
    case_id: str
    indoor_f: float | None = None  # lets the agent pass a reading it learned after create_case


def rule_text(at: datetime, indoor_f: float, outdoor_f: float | None, violation: bool | None) -> str:
    if not heat.in_season(at):
        return "Outside heat season (Oct 1 - May 31): no minimum applies"
    if heat.is_night(at):
        return f"Night rule (10pm-6am): inside {indoor_f:.0f}F {'<' if violation else '>='} 62F"
    if outdoor_f is None:
        return "Day rule (6am-10pm): outdoor temperature unavailable, so it can't be checked"
    if outdoor_f >= 55:
        return f"Day rule: outside {outdoor_f:.0f}F is not below 55F, so no minimum applies"
    return f"Day rule: outside {outdoor_f:.0f}F < 55F, inside {indoor_f:.0f}F {'<' if violation else '>='} 68F"


@app.post("/api/tools/check_violation", dependencies=[Depends(tool_auth)])
async def check_violation(body: CheckViolation) -> dict:
    case = hcdb.get_case(body.case_id)
    if case is None:
        return {"error": "no case with that case_id; call create_case first"}
    indoor = body.indoor_f if body.indoor_f is not None else case["indoor_f"]
    if indoor is None:
        return {"error": "no indoor temperature yet; ask the resident what their thermometer reads"}
    at = datetime.now(timezone.utc)
    demo = os.environ.get("HC_DEMO_OUTDOOR_F")
    # demo only: a warm afternoon would make the day rule moot on stage. The override is
    # named in the rule text, so the screen never passes a simulated reading off as live
    outdoor = float(demo) if demo else await heat.outdoor_temp_f(at)  # live: never raises
    violation = heat.is_violation(at, indoor, outdoor)
    rule = rule_text(at, indoor, outdoor, violation) + (" (demo: simulated cold day)" if demo else "")
    hcdb.update_case(case["id"], indoor_f=indoor, outdoor_f=outdoor, is_violation=violation,
                     rule_applied=rule)
    return {"is_violation": violation, "outdoor_f": outdoor, "required_f": heat.required_f(at, outdoor),
            "rule_applied": rule, "fine_range": FINE_RANGE}


class SubmitCase(BaseModel):
    case_id: str
    resident_confirmed: bool = False


@app.post("/api/tools/submit_case", dependencies=[Depends(tool_auth)])
async def submit_case(body: SubmitCase, tasks: BackgroundTasks) -> dict:
    if not body.resident_confirmed:
        # the resident's spoken yes is the human check on this side; no yes, no submit
        return {"ok": False, "error": "only submit after the resident says yes to sending it"}
    case = hcdb.get_case(body.case_id)
    if case is None:
        return {"ok": False, "error": "no case with that case_id; call create_case first"}
    if case["status"] == "intake":
        case = hcdb.update_case(case["id"], status="submitted")
        hcdb.add_event(case["id"], "submitted", "resident")
        # building + priority after the response, so the case reaches the console complete
        # while the resident is still on the line (webhooks.enrich never raises)
        tasks.add_task(webhooks.enrich, case["id"])
        await send_sms(case, "confirmation",
                       f"Heat Check: your no-heat complaint {case['case_number']} was sent to the "
                       f"city caseworker. We'll call you back in {case['language_label']}.")
    return {"ok": True, "case_number": case["case_number"]}


# ---- callback agent tool ----

class ResidentResponse(BaseModel):
    case_id: str
    response: str
    note_original: str | None = None
    note_en: str | None = None


@app.post("/api/tools/record_resident_response", dependencies=[Depends(tool_auth)])
async def record_resident_response(body: ResidentResponse) -> dict:
    if body.response not in RESPONSES:
        return {"ok": False, "error": f"response must be one of: {', '.join(RESPONSES)}"}
    case = hcdb.get_case(body.case_id)
    if case is None:
        return {"ok": False, "error": "no case with that case_id"}
    case = hcdb.update_case(case["id"], status="called_back", resident_response=body.response,
                            resident_response_note_en=body.note_en)
    hcdb.add_event(case["id"], "resident_response", "resident", text_original=body.note_original,
                   text_en=body.note_en, payload={"response": body.response})
    action = ACTIONS.get(case["caseworker_action"] or "", "your case was reviewed")
    await send_sms(case, "receipt", f"Heat Check {case['case_number']}: {action}. "
                                     f"Your answer: {RESPONSES[body.response]}. Reply anytime.")
    return {"ok": True}


# ---- intake agent: mid-call photo request ----

class RequestPhoto(BaseModel):
    case_id: str


@app.post("/api/tools/request_photo", dependencies=[Depends(tool_auth)])
async def request_photo(body: RequestPhoto) -> dict:
    case = hcdb.get_case(body.case_id)
    if case is None:
        return {"ok": False, "error": "no case with that case_id; call create_case first"}
    event = await send_sms(case, "photo_request",
                           f"Heat Check {case['case_number']}: reply to this message with a photo "
                           f"of your thermometer or radiator. It helps prove the heat is out.")
    # the call goes on either way; the agent just shouldn't promise a message that failed
    if event and event["payload"].get("status") == "sent":
        return {"ok": True, "result": "Sent. Tell them a message just arrived on their phone and "
                                      "they can reply with the photo now or after the call."}
    return {"ok": True, "result": "The message may not arrive. Tell them the caseworker can "
                                  "still take the complaint without a photo, and carry on."}


# ---- dashboard ----

@app.get("/api/cases")
def list_cases() -> list[dict]:
    return hcdb.list_cases()


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict:
    return {**fetch(case_id), "events": hcdb.list_events(case_id)}


class Action(BaseModel):
    caseworker_action: str
    caseworker_message_en: str


@app.post("/api/cases/{case_id}/action")
def caseworker_action(case_id: str, body: Action) -> dict:
    if body.caseworker_action not in ACTIONS:
        raise HTTPException(status_code=422, detail=f"caseworker_action must be one of: {', '.join(ACTIONS)}")
    if not body.caseworker_message_en.strip():
        raise HTTPException(status_code=422, detail="caseworker_message_en is empty")
    case = fetch(case_id)
    hcdb.update_case(case["id"], caseworker_action=body.caseworker_action,
                     caseworker_message_en=body.caseworker_message_en.strip(),
                     status="callback_requested")
    hcdb.add_event(case["id"], "caseworker_action", "caseworker",
                   text_en=body.caseworker_message_en.strip(),
                   payload={"action": body.caseworker_action})
    return {"ok": True}


@app.post("/api/cases/{case_id}/callback")
async def callback(case_id: str) -> dict:
    case = fetch(case_id)
    # the caseworker's approved message is the human check on this side: no message, no call
    if not case["caseworker_message_en"] or case["status"] not in ("callback_requested", "called_back"):
        raise HTTPException(status_code=409, detail="save a caseworker action and message first")
    try:
        conversation_id = await elevenlabs.outbound_call(
            elevenlabs.env("ELEVENLABS_CALLBACK_AGENT_ID"), case["resident_phone"],
            {"case_id": case["id"], "resident_name": case["resident_name"] or "",
             "language": case["language"], "language_label": case["language_label"] or "",
             "case_number": case["case_number"], "address": case["address"],
             "caseworker_action": case["caseworker_action"] or "",
             "caseworker_message_en": case["caseworker_message_en"]},
            language=case["language"])
    except elevenlabs.NotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"ElevenLabs outbound call failed: {e}")
    hcdb.add_event(case["id"], "callback_call", "callback_agent",
                   payload={"conversation_id": conversation_id})
    return {"ok": True, "conversation_id": conversation_id}


class CaseworkerText(BaseModel):
    text_en: str


@app.post("/api/cases/{case_id}/sms", status_code=201)
async def caseworker_text(case_id: str, body: CaseworkerText) -> dict:
    if not body.text_en.strip():
        raise HTTPException(status_code=422, detail="text_en is empty")
    if sms is None:
        raise HTTPException(status_code=503, detail="sms module missing")
    case = fetch(case_id)
    # the caseworker wrote it and clicked Send: that is the human check, so no template
    return await sms.send_sms(case, "caseworker", body.text_en.strip())


@app.get("/api/events/{event_id}/media/{n}")
async def event_media(event_id: str, n: int) -> Response:
    event = hcdb.get_event(event_id)
    media = ((event or {}).get("payload") or {}).get("media") or []
    if sms is None or not 0 <= n < len(media):
        raise HTTPException(status_code=404, detail="no such media")
    got = await sms.fetch_media(media[n]["twilio_url"])
    if got is None:
        raise HTTPException(status_code=404, detail="Twilio did not return the media")
    content, content_type = got
    return Response(content, media_type=content_type,
                    headers={"Cache-Control": "private, max-age=3600"})


@app.get("/health")
def health() -> dict:
    return {"ok": True}
