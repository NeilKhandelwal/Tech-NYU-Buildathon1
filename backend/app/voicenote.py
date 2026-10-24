"""WhatsApp voice notes, transcribed by ElevenLabs speech-to-text. A resident who can't type,
or would rather speak Hindi than spell it, holds the mic button and talks; the caseworker
reads it on the case like any other reply. Never raises: a note that can't be transcribed is
still logged, with a placeholder the caseworker can see."""
import logging
import os

import httpx

logger = logging.getLogger("backend")

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
# newest first; a 4xx on a model id falls through to the next, so a retired or not-yet-enabled
# model doesn't cost the demo its transcript
STT_MODELS = ("scribe_v2", "scribe_v1")
UNTRANSCRIBED = "[voice message — could not be transcribed]"


def audio_url(form: dict) -> tuple[str, str] | None:
    """(Twilio media URL, content type) of the first audio attachment, if there is one."""
    for n in range(int(form.get("NumMedia") or 0)):
        content_type = form.get(f"MediaContentType{n}", "")
        if content_type.startswith("audio/") and form.get(f"MediaUrl{n}"):
            return form[f"MediaUrl{n}"], content_type
    return None


async def fetch_audio(url: str) -> bytes | None:
    """The note's bytes from Twilio, which needs our account's auth to hand them over."""
    sid, token = os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN")
    if not (sid and token and url.startswith("https://api.twilio.com/")):
        return None  # only Twilio's own URLs: a forged webhook must not make us fetch anything
    try:
        # Twilio answers with a redirect to where the file is stored
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, auth=(sid, token))
        return r.content if r.status_code < 400 else None
    except Exception:
        logger.exception("fetching the voice note from Twilio failed")
        return None


async def transcribe(audio: bytes, content_type: str) -> tuple[str, str | None] | None:
    """(text, language code ElevenLabs heard) or None."""
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        return None
    for model in STT_MODELS:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(STT_URL, headers={"xi-api-key": key},
                                      data={"model_id": model},
                                      files={"file": ("note", audio, content_type)})
        except Exception:
            logger.exception("ElevenLabs speech-to-text failed")
            return None
        if r.status_code < 400:
            body = r.json()
            text = (body.get("text") or "").strip()
            return (text, body.get("language_code")) if text else None
        logger.warning("speech-to-text with %s: %s %s", model, r.status_code, r.text[:200])
        if r.status_code not in (400, 404, 422):  # not a model problem: another model won't help
            return None
    return None


async def from_form(form: dict) -> dict | None:
    """For an inbound message carrying a voice note: {text, payload}, where text is what the
    resident said (or a placeholder) and payload goes on the sms_in event. None if no audio."""
    found = audio_url(form)
    if found is None:
        return None
    url, content_type = found
    audio = await fetch_audio(url)
    heard = await transcribe(audio, content_type) if audio else None
    text, language = heard or (UNTRANSCRIBED, None)
    return {"text": text, "payload": {"voice_note": True, "transcribed": heard is not None,
                                      "heard_language": language, "content_type": content_type}}
