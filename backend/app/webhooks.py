"""CityLine webhooks (CONTRACT.md "Webhooks"): ElevenLabs post-call and Twilio inbound SMS.

The post-call webhook answers 200 at once and does the slow work (LLM, Open Data) after the
response, so ElevenLabs never times out and retries a call we already have.
"""
import json
import logging
import os
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from app import building, elevenlabs, llm

logger = logging.getLogger("backend")

router = APIRouter()


def hcdb():
    from app import hcdb as module  # lane A's module; late import so this file loads alone
    return module


def digits(phone: str | None) -> str:
    d = "".join(c for c in phone or "" if c.isdigit())
    return d[-10:]  # cases store 10-digit US numbers; Twilio/ElevenLabs send +1…


def find_case(data: dict) -> dict | None:
    db = hcdb()
    if data.get("conversation_id"):
        case = db.find_case_by_conversation(data["conversation_id"])
        if case:
            return case
    phone_call = (data.get("metadata") or {}).get("phone_call") or {}
    phone = digits(phone_call.get("external_number"))
    return db.find_case_by_phone(phone) if phone else None


def is_callback(data: dict) -> bool:
    callback_agent = os.environ.get("ELEVENLABS_CALLBACK_AGENT_ID")
    if callback_agent:
        return data.get("agent_id") == callback_agent
    variables = (data.get("conversation_initiation_client_data") or {}).get("dynamic_variables") or {}
    return "caseworker_message_en" in variables  # only the callback is dialed with it


def collected_english(data: dict) -> str | None:
    """The agent's own English transcript (provision.py's `transcript_en` data-collection
    item): the translation when no LLM key is set."""
    results = (data.get("analysis") or {}).get("data_collection_results") or {}
    value = (results.get("transcript_en") or {}).get("value")
    return value if isinstance(value, str) and value.strip() else None


async def process_intake(case: dict, data: dict) -> None:
    transcript = llm.transcript_text(data.get("transcript") or [])
    result = await llm.analyze(transcript, case)
    found = await building.building_lookup(case.get("address") or "", case.get("borough"))
    fields = {
        "transcript_original": transcript,
        "transcript_en": result["transcript_en"] or collected_english(data),
        # CLAUDE.md fallback: no LLM summary -> the agent's own post-call summary
        "summary_en": result["summary_en"] or (data.get("analysis") or {}).get("transcript_summary"),
        "suggested_action": result["suggested_action"],
        "priority": llm.priority(case, found),
        "building": found,
    }
    if case.get("status") == "submitted":
        fields["status"] = "in_review"  # a case the resident never confirmed stays in intake
    hcdb().update_case(case["id"], **fields)


async def enrich(case_id) -> None:
    """Run at submit, while the resident is still on the line: building history, priority and
    the rule's suggested action, so the case doesn't land on the console as an empty 'normal'.
    The post-call webhook later adds transcript and summary and recomputes both. Never raises."""
    try:
        case = hcdb().get_case(case_id)
        if case is None:
            return
        found = await building.building_lookup(case.get("address") or "", case.get("borough"))
        fields = {"building": found, "priority": llm.priority(case, found)}
        if not case.get("suggested_action"):
            fields["suggested_action"] = llm.suggest(case)
        hcdb().update_case(case_id, **fields)
    except Exception:
        logger.exception("enrich failed for case %s", case_id)


async def process_callback(case: dict, data: dict) -> None:
    transcript = llm.transcript_text(data.get("transcript") or [])
    english = transcript if llm.is_english(case.get("language")) else \
        (await llm.translate(transcript, "en") or collected_english(data))
    db = hcdb()
    db.add_event(case["id"], "callback_call", "callback_agent", text_original=transcript,
                 text_en=english, payload={"conversation_id": data.get("conversation_id"), "stage": "ended",
                                           "summary": (data.get("analysis") or {}).get("transcript_summary")})
    if not case.get("resident_response"):
        # the agent never recorded an answer: voicemail, hang-up or no pickup
        db.update_case(case["id"], resident_response="no_answer")


async def process(data: dict) -> None:
    try:
        case = find_case(data)
        if case is None:
            logger.warning("post-call for unknown conversation %s", data.get("conversation_id"))
            return
        await (process_callback if is_callback(data) else process_intake)(case, data)
    except Exception:
        logger.exception("post-call processing failed for %s", data.get("conversation_id"))


@router.post("/api/webhooks/elevenlabs_post_call")
async def elevenlabs_post_call(request: Request, tasks: BackgroundTasks) -> dict:
    raw = await request.body()  # the signature is over the raw bytes
    secret = os.environ.get("ELEVENLABS_WEBHOOK_SECRET")
    if secret and not elevenlabs.signature_ok(raw, request.headers.get("elevenlabs-signature"), secret):
        raise HTTPException(status_code=401, detail="bad ElevenLabs-Signature")
    event = json.loads(raw)
    if event.get("type") == "post_call_transcription":
        tasks.add_task(process, event.get("data") or {})
    return {"ok": True}  # other event types (audio, initiation failure): ignored, not retried


@router.post("/api/webhooks/twilio_sms")
async def twilio_sms(request: Request) -> Response:
    # form-encoded; parsed by hand so FastAPI's Form() doesn't pull in python-multipart
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    from app import sms  # late, like hcdb(): this file must load without lane modules
    sender = "".join(c for c in form.get("From", "") if c.isdigit())
    # a WhatsApp alias maps back to the case's phone; hcdb.digits keeps a non-US country code
    phone = hcdb().digits(sms.case_phone(sender)) if sender else ""
    body = form.get("Body", "").strip()
    case = hcdb().find_case_by_phone(phone) if phone else None
    if case and body:
        english = body if llm.is_english(case.get("language")) else await llm.translate(body, "en")
        hcdb().add_event(case["id"], "sms_in", "resident", text_original=body, text_en=english,
                         payload={"from": phone})
    # empty TwiML: no auto-reply text
    return Response('<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
                    media_type="application/xml")
