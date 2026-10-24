"""Optional translation for subtitles and tenant questions (CONTRACT "## Tenant advocate").

One Gemini `generateContent` request per text. Translation is a nicety, never a dependency:
no key, a timeout or any odd response means None, and the caller shows the original.
"""
import logging
import os

import httpx

logger = logging.getLogger("backend")

API = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.6-flash"
# short: a subtitle that arrives five seconds late is already useless
TIMEOUT_S = 5


def prompt(text: str, target_lang: str) -> str:
    return (f"Translate the text below into the language with BCP-47 tag '{target_lang}'. "
            "Reply with only the translation: no quotes, notes or explanations.\n\n" + text)


def reply_text(body: dict) -> str | None:
    """The first candidate's text, or None for anything else (blocked prompt, empty reply)."""
    try:
        parts = body["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return text or None


async def translate(text: str, target_lang: str) -> str | None:
    """`text` in `target_lang`, or None when translation is off or failed. Never raises.

    The callers know the source language (agent and landlord speak English, the tenant speaks
    tenant_language), so they skip same-language pairs; here only the free checks happen."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key or not text.strip() or not target_lang:
        return None  # off, or nothing to say: no request at all
    model = os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.post(f"{API}/{model}:generateContent",
                                  headers={"x-goog-api-key": key},
                                  json={"contents": [{"parts": [{"text": prompt(text, target_lang)}]}]})
            r.raise_for_status()
            return reply_text(r.json())
    except Exception as e:  # httpx errors, bad JSON, anything: a missing subtitle, not a 500
        logger.warning("translation to %s failed: %s", target_lang, e)
        return None
