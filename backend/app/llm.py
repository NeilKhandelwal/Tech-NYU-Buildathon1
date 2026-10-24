"""LLM work for Heat Check (CONTRACT.md "Internal helpers"): translation for SMS and
transcripts, and the post-call analysis the caseworker reads.

Grok first (XAI_API_KEY, OpenAI-compatible chat completions), Gemini second (translate.py),
and a plain fallback when neither is set. Never raises: a missing translation shows the
original, a missing summary falls back to ElevenLabs' own summary.
"""
import json
import logging
import os

import httpx

from app import translate as gemini

logger = logging.getLogger("backend")

XAI_URL = "https://api.x.ai/v1/chat/completions"
DEFAULT_XAI_MODEL = "grok-4.7"
TIMEOUT_S = 20
ACTIONS = ["schedule_inspection", "need_more_info", "emergency_repair", "resolved"]


async def grok(prompt: str, json_mode: bool = False) -> str | None:
    key = os.environ.get("XAI_API_KEY")
    if not key:
        return None
    body: dict = {"model": os.environ.get("XAI_MODEL") or DEFAULT_XAI_MODEL,
                  "messages": [{"role": "user", "content": prompt}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.post(XAI_URL, headers={"Authorization": f"Bearer {key}"}, json=body)
            r.raise_for_status()
            return (r.json()["choices"][0]["message"]["content"] or "").strip() or None
    except Exception as e:  # any failure is a fallback, not a 500
        logger.warning("grok failed: %s", e)
        return None


def is_english(lang: str | None) -> bool:
    return not lang or lang.lower().split("-")[0] == "en"


async def translate(text: str, target_lang: str) -> str | None:
    """`text` in `target_lang`, or None when no LLM answered. Skipping same-language pairs is
    the caller's job (it knows the source language)."""
    if not text or not text.strip():
        return None
    out = await grok(gemini.prompt(text, target_lang))
    return out or await gemini.translate(text, target_lang)


def transcript_text(turns: list[dict]) -> str:
    """ElevenLabs transcript turns as 'Agent: …' / 'Resident: …' lines."""
    lines = []
    for t in turns or []:
        text = (t.get("message") or "").strip()
        if text and t.get("role") in ("agent", "user"):
            lines.append(f"{'Agent' if t['role'] == 'agent' else 'Resident'}: {text}")
    return "\n".join(lines)


def analysis_prompt(transcript: str, case: dict) -> str:
    return (
        "You help a city caseworker triage a no-heat complaint phoned in to an AI intake line.\n"
        f"Case facts: indoor {case.get('indoor_f')}F, outdoor {case.get('outdoor_f')}F, "
        f"possible violation: {case.get('is_violation')}, days without heat: "
        f"{case.get('days_without_heat')}, language: {case.get('language')}.\n"
        "Reply with only a JSON object with keys:\n"
        '  "transcript_en": the full transcript translated to English, keeping the '
        "'Agent:' / 'Resident:' line prefixes (copy it unchanged if already English),\n"
        '  "summary_en": two or three plain English sentences for the caseworker,\n'
        f'  "suggested_action": one of {ACTIONS}.\n\nTranscript:\n{transcript}')


async def analyze(transcript: str, case: dict) -> dict:
    """{transcript_en, summary_en, suggested_action} from the LLM; any key it can't give is
    None (suggested_action falls back to the rule in `suggest`)."""
    out: dict = {"transcript_en": None, "summary_en": None, "suggested_action": None}
    raw = await grok(analysis_prompt(transcript, case), json_mode=True)
    if raw is None:  # no Grok: translate with Gemini at least, the summary falls back upstream
        out["transcript_en"] = transcript if is_english(case.get("language")) else \
            await gemini.translate(transcript, "en")
    else:
        try:
            parsed = json.loads(raw.strip().removeprefix("```json").removesuffix("```"))
            out.update({k: parsed.get(k) for k in out if isinstance(parsed.get(k), str)})
        except (ValueError, AttributeError):
            logger.warning("grok analysis was not JSON")
    if out["suggested_action"] not in ACTIONS:
        out["suggested_action"] = suggest(case)
    return out


def suggest(case: dict) -> str:
    """The rule behind the suggestion when no LLM gives a valid one."""
    if case.get("is_violation") and (case.get("indoor_f") or 99) < 58:
        return "emergency_repair"  # HPD's Emergency Repair Program threshold at night
    if case.get("indoor_f") is None or not case.get("address"):
        return "need_more_info"
    return "schedule_inspection"


def priority(case: dict, building: dict | None) -> str:
    """Deterministic (no LLM): the same facts always rank the same way, and the caseworker
    can see why. urgent | high | normal."""
    indoor = case.get("indoor_f")
    level = "normal"
    if case.get("is_violation"):
        level = "high"
        if (indoor is not None and indoor < 60) or (case.get("days_without_heat") or 0) >= 3:
            level = "urgent"
    if building and building.get("repeat_offender") and level == "normal":
        level = "high"
    return level
