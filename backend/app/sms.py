"""Twilio SMS for Heat Check (CONTRACT.md `sendSms`). Every text is written in English,
translated into the resident's language, sent, and logged as an `sms_out` event even when
sending fails or Twilio isn't configured, so the dashboard's SMS panel always shows it."""
import logging
import os

import httpx

from app import llm

logger = logging.getLogger("backend")

TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{}/Messages.json"

async def twilio_send(to_digits: str, body: str) -> tuple[str, str | None, str | None]:
    """(status, sid, error); status sent | failed | skipped. Never raises."""
    sid, token, from_ = (os.environ.get(k) for k in
                         ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"))
    if not (sid and token and from_):
        return "skipped", None, None
    # hcdb.digits stores US numbers as ten digits and keeps any other country code, e.g. a
    # +91 WhatsApp number is 919108651858
    to = "+1" + to_digits if len(to_digits) == 10 else "+" + to_digits
    # US texts from an unregistered number die with 30034 (A2P 10DLC takes days), so the demo
    # sends over WhatsApp: same API, both ends prefixed, From is the sandbox number
    if os.environ.get("TWILIO_CHANNEL") == "whatsapp":
        to = "whatsapp:" + to
        from_ = "whatsapp:" + os.environ.get("TWILIO_WHATSAPP_NUMBER", "+14155238886")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(TWILIO_URL.format(sid), auth=(sid, token),
                                  data={"To": to, "From": from_, "Body": body})
        if r.status_code >= 400:
            # Twilio's error body names the cause (e.g. 21608: trial account, unverified number)
            return "failed", None, r.text[:300]
        return "sent", r.json().get("sid"), None
    except Exception as e:
        return "failed", None, str(e)


async def send_sms(case: dict, kind: str, text_en: str, body: str | None = None) -> dict:
    """Translate, send, log. Returns the `sms_out` event. `body` is a text already in the
    resident's language (hc.py's pre-written templates): sent as-is, no LLM needed."""
    from app import hcdb  # lane A's module; imported late so this file loads without it
    lang = case.get("language") or "en"
    if body is None:
        body = text_en if llm.is_english(lang) else (await llm.translate(text_en, lang) or text_en)
    phone = "".join(c for c in case.get("resident_phone") or "" if c.isdigit())
    status, sid, error = await twilio_send(phone, body) if phone else ("failed", None, "no phone")
    if status != "sent":
        logger.warning("sms %s for %s: %s %s", kind, case.get("case_number"), status, error or "")
    return hcdb.add_event(case["id"], "sms_out", "system", text_original=body, text_en=text_en,
                          payload={"kind": kind, "to": phone, "status": status, "sid": sid,
                                   "error": error})
