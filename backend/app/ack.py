"""The instant reply to a resident's WhatsApp message: "got it, your caseworker can read it",
in their language, quoting back what we understood. Without it the resident sends a voice note
into silence and can't tell whether anyone heard them. Logged as an sms_out (kind 'ack') like
every other text, so the console shows it too. Never raises."""
import logging
import os

logger = logging.getLogger("backend")

# Pre-written for the demo languages so the reply is instant and needs no LLM; any other
# language is translated by sms.py from the English. {said} is the resident's own words.
ACK = {
    "en": {"voice": 'CityLine {case_number}: we got your voice message. We heard: "{said}". '
                    "Your caseworker can read it now.",
           "text": "CityLine {case_number}: we got your message. Your caseworker can read it now.",
           "unclear": "CityLine {case_number}: we got your voice message but couldn't make it out. "
                      "Please try again, or type your message."},
    "hi": {"voice": 'CityLine {case_number}: आपका वॉइस संदेश मिल गया। हमने सुना: "{said}"। '
                    "आपके केसवर्कर इसे अभी पढ़ सकते हैं।",
           "text": "CityLine {case_number}: आपका संदेश मिल गया। आपके केसवर्कर इसे अभी पढ़ सकते हैं।",
           "unclear": "CityLine {case_number}: आपका वॉइस संदेश मिला, पर हम उसे समझ नहीं पाए। "
                      "कृपया फिर से भेजें या लिखकर भेजें।"},
    "es": {"voice": 'CityLine {case_number}: recibimos su mensaje de voz. Entendimos: "{said}". '
                    "Su encargado de casos ya puede leerlo.",
           "text": "CityLine {case_number}: recibimos su mensaje. Su encargado de casos ya puede leerlo.",
           "unclear": "CityLine {case_number}: recibimos su mensaje de voz, pero no pudimos "
                      "entenderlo. Inténtelo de nuevo o escriba su mensaje."},
}


def which(note: dict | None) -> str:
    if note is None:
        return "text"
    return "voice" if note["payload"].get("transcribed") else "unclear"


async def acknowledge(case: dict, said: str, note: dict | None) -> None:
    """Text the resident that their message (`said`, in their words) reached the caseworker."""
    from app import sms  # late, like webhooks.py: this file must load without lane modules
    if not os.environ.get("TWILIO_ACCOUNT_SID"):
        return  # a "got it" that can't be sent is only noise on the case's message panel
    try:
        kind = which(note)
        lang = case.get("language") or "en"
        fields = {"case_number": case["case_number"], "said": said}
        english = ACK["en"][kind].format(**fields)
        local = ACK.get(lang, {}).get(kind)
        # None lets sms.py translate the English for a language we have no template for
        body = local.format(**fields) if local and lang != "en" else None
        await sms.send_sms(case, "ack", english, body=body)
    except Exception:
        logger.exception("acknowledging an inbound message failed")
