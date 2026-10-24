"""LLM work for CityLine (CONTRACT.md "Internal helpers"): translation for SMS and
transcripts, and the post-call analysis the caseworker reads.

Grok first (XAI_API_KEY, OpenAI-compatible chat completions), then Claude (ANTHROPIC_API_KEY,
official SDK), then Gemini (translate.py), and a plain fallback when none is set. Never raises: a missing translation shows the
original, a missing summary falls back to ElevenLabs' own summary.
"""
import json
import logging
import os

import anthropic
import httpx

from app import translate as gemini

logger = logging.getLogger("backend")

XAI_URL = "https://api.x.ai/v1/chat/completions"
DEFAULT_XAI_MODEL = "grok-4.7"
DEFAULT_CLAUDE_MODEL = "claude-haiku-5-5"
TIMEOUT_S = 20


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


def claude_client() -> anthropic.AsyncAnthropic:
    # one retry: a webhook background task can wait, but not through the SDK's default backoff
    return anthropic.AsyncAnthropic(timeout=TIMEOUT_S, max_retries=1)


async def claude(prompt: str) -> str | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        response = await claude_client().messages.create(
            model=os.environ.get("ANTHROPIC_MODEL") or DEFAULT_CLAUDE_MODEL,
            max_tokens=4096,
            # translation and a short summary: low effort is plenty, and keeps it fast
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}])
    except anthropic.APIError as e:  # status and connection errors alike: fall back, never raise
        logger.warning("claude failed: %s", e)
        return None
    if response.stop_reason == "refusal":
        logger.warning("claude refused: %s", response.stop_details)
        return None
    # thinking is on by default on Haiku 5.5, so the text block isn't necessarily first
    text = next((b.text for b in response.content if b.type == "text"), "").strip()
    return text or None


async def ask(prompt: str, json_mode: bool = False) -> str | None:
    """The first configured LLM's answer: Grok, then Claude. None when neither answered."""
    return await grok(prompt, json_mode) or await claude(prompt)


def is_english(lang: str | None) -> bool:
    return not lang or lang.lower().split("-")[0] == "en"


async def translate(text: str, target_lang: str) -> str | None:
    """`text` in `target_lang`, or None when no LLM answered. Skipping same-language pairs is
    the caller's job (it knows the source language)."""
    if not text or not text.strip():
        return None
    out = await ask(gemini.prompt(text, target_lang))
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
        '  "summary_en": two or three plain English sentences for the caseworker.\n'
        f"Reply with only the JSON object, no other text.\n\nTranscript:\n{transcript}")


async def analyze(transcript: str, case: dict) -> dict:
    """{transcript_en, summary_en} from the LLM (None where it can't give one) plus
    suggested_action, which is always the rule in `suggest`: the same facts must always
    suggest the same action, and the caseworker can see why."""
    out: dict = {"transcript_en": None, "summary_en": None}
    raw = await ask(analysis_prompt(transcript, case), json_mode=True)
    if raw is None:  # no Grok/Claude: translate with Gemini at least, the summary falls back upstream
        out["transcript_en"] = transcript if is_english(case.get("language")) else \
            await gemini.translate(transcript, "en")
    else:
        try:
            parsed = json.loads(raw.strip().removeprefix("```json").removesuffix("```"))
            out.update({k: parsed.get(k) for k in out if isinstance(parsed.get(k), str)})
        except (ValueError, AttributeError):
            logger.warning("grok analysis was not JSON")
    out["suggested_action"] = suggest(case)
    return out


def suggest(case: dict) -> str:
    """The suggested next step, by rule (no LLM)."""
    if case.get("complaint_type", "heat") not in ("heat", "hot_water"):
        # a pothole or noise complaint has no temperature to ask for
        return "schedule_inspection" if case.get("address") else "need_more_info"
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
