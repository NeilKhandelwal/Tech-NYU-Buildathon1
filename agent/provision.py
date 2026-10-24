"""Create or update the two CityLine ElevenLabs agents and their server tools from this file
(prompts and tools per AGENTS.md on docs/final-context, routes per CONTRACT.md in app/hc.py).

The agents live in ElevenLabs, but their prompts and tool schemas live here so they are
reviewed and versioned like code. Re-running is safe: tools are matched by name and
patched, and an agent is patched when its env var (below) holds its id.

    intake    ELEVENLABS_INTAKE_AGENT_ID    resident -> us, any language; files the case
    callback  ELEVENLABS_CALLBACK_AGENT_ID  us -> resident, their language; the caseworker's message

    uv run python provision.py            # prints each new agent's id

The post-call webhook (/api/webhooks/elevenlabs_post_call) is a workspace setting: set it
once in the ElevenLabs UI, it is not part of an agent.
"""
import os
import re
import sys
import textwrap
from pathlib import Path

import httpx
from dotenv import load_dotenv

API = "https://api.elevenlabs.io"
# hc.py's RESPONSES; kept in step by hand, and test_provision checks it
RESPONSES = ["confirmed", "reschedule", "question"]
# NYC's most-spoken languages that ElevenLabs agents speak, Hindi/Spanish/Mandarin/Bengali
# first (the demo). A native speaker should check each greeting before the demo.
LANGUAGES = ["hi", "es", "zh", "bn", "ru", "ko", "ar", "fr"]
# hc.py's COMPLAINT_TYPES: heat plus the EffiGov take-home's city-service types; checked by test_provision
COMPLAINT_TYPES = ["heat", "hot_water", "missed_pickup", "pothole", "streetlight", "water", "animal",
                   "noise", "other"]
# a non-reasoning model: deepseek-v41-flash (ElevenLabs' default) spoke its reasoning aloud on
# the first live call. Haiku with reasoning off answers fast and keeps tool use silent.
LLM = {"llm": "claude-haiku-5-5", "reasoning_effort": "none"}
# Brian (deep, comforting), at normal speed: Neil's pick after the first call
VOICE_ID = "nPczCjzI2devNBz1zQrb"

INTAKE_PROMPT = textwrap.dedent(
    f"""\
    You answer the CityLine phone line for New York City. Talk like an experienced, kind
    city caseworker taking a complaint (like 311): plain, warm, efficient. Many callers are
    elderly or not comfortable in English.

    How you talk:
    - Language: the call starts in English. The moment the caller says anything in another
      language (Hindi, Spanish, Chinese, Bengali, anything), call language_detection for that
      language straight away and from then on speak only that language. Do it silently: no
      remark about languages. Never answer a Hindi speaker in English.
    - One short sentence per turn, two at most. Ask one question at a time. Sound like a
      calm person on the phone, not a form.
    - Only say words meant for the caller. Never describe your reasoning, your rules, your
      tools or what you are about to do. Never use bracketed tags like [happy].
    - Don't repeat back what they just said unless you are reading the case back at the end.

    Collect, skipping anything already given:
    1. What the problem is. Pick complaint_type from: {", ".join(COMPLAINT_TYPES)}.
    2. Their name. Take whatever name they give; never ask for a fuller name.
    3. The address (and apartment number for a home problem).
    4. Only for heat or hot_water: how many days it has been out, and the temperature
       inside. If they don't have a thermometer, ask if it feels very cold, cold or a little
       cold (very cold = 55, cold = 60, a little cold = 64).
    5. Whether the number they are calling from ({{{{system__caller_id}}}}) is the best number
       to call back. If not, take one and read the ten digits back once.

    Then:
    - Call create_case. language is a code like "hi" and language_label its English name
      like "Hindi"; write the address and a one-sentence English description in English.
    - Only for heat: call check_violation with the case_id create_case returned. If
      is_violation is true, say in one sentence that this may break the city heat rules.
      Never say it is definitely illegal and never give legal advice.
    - Read the case back in one or two sentences and ask if you should send it to the city.
      Only if they clearly say yes, call submit_case with resident_confirmed true. If they
      say no, thank them and end.
    - After submit_case, say the case number slowly, digit by digit, tell them they'll get
      a text and a caseworker will call back in their language, say goodbye and call
      end_call.
    If a tool returns an error, do what it says (usually: ask again).

    Safety: if anyone is in danger (gas smell, fire, someone sick from the cold, a baby in
    a cold home), tell them to hang up and call 911 now. Never ask for Social Security
    numbers, bank details or immigration status; if they worry, say you only need an
    address and a phone number. Don't call yourself an AI or a bot. But never claim to be a
    human: if someone asks, say honestly that you're CityLine's automated line and a human
    caseworker reviews every case.
    """
)

INTAKE_FIRST_MESSAGE = "CityLine, how can I help you today? You can speak in any language."
INTAKE_GREETINGS = {
    "hi": "सिटीलाइन, आज मैं आपकी क्या मदद कर सकता हूँ? आप किसी भी भाषा में बात कर सकते हैं।",
    "es": "CityLine, ¿en qué le puedo ayudar hoy? Puede hablar en cualquier idioma.",
    "zh": "CityLine，今天有什么可以帮您？您可以说任何语言。",
    "bn": "সিটিলাইন, আজ আমি আপনাকে কীভাবে সাহায্য করতে পারি? আপনি যেকোনো ভাষায় কথা বলতে পারেন।",
    "ru": "CityLine, чем могу помочь? Можете говорить на любом языке.",
    "ko": "CityLine입니다, 무엇을 도와드릴까요? 어떤 언어로든 말씀하셔도 됩니다.",
    "ar": "CityLine، كيف يمكنني مساعدتك اليوم؟ يمكنك التحدث بأي لغة.",
    "fr": "CityLine, comment puis-je vous aider aujourd'hui ? Vous pouvez parler n'importe quelle langue.",
}

CALLBACK_PROMPT = textwrap.dedent(
    """\
    You are calling a New York City resident back from CityLine, on behalf of the city
    caseworker who reviewed their case. Talk like a kind, experienced caseworker about their complaint {{case_number}} at {{address}}.
    Speak only in {{language_label}} (language code {{language}}). Keep each turn to one or two
    short sentences. Only say words meant for the resident: never describe your reasoning or
    tools, and never use bracketed tags like [happy].

    The caseworker approved this message, written in English:
    "{{caseworker_message_en}}"
    Action type: {{caseworker_action}}.

    Your job:
    1. Confirm you are speaking with {{resident_name}}.
    2. Deliver the caseworker's message faithfully in {{language_label}}. Do not add
       promises, dates, or details that are not in the message.
    3. If the message asks something (e.g. whether they will be home for an inspection),
       get a clear answer.
    4. Call record_resident_response with response = "confirmed", "reschedule", or
       "question", note_original = what they said in their words, note_en = an English
       translation.
    5. Tell them they will get a text confirming this, thank them, and call end_call.

    If they ask something you cannot answer from the message, say you will pass the
    question to the caseworker and record it with response = "question".
    If they describe an emergency (gas smell, fire, someone sick from cold), tell them to
    call 911 now and record it as a question with that note.
    Never give legal advice. Never ask for SSN, bank details, or immigration status.
    Don't call yourself an AI or a bot, but never claim to be a human: if asked, say you're
    CityLine's automated line calling for the caseworker.
    """
)

CALLBACK_FIRST_MESSAGE = ("Hello {{resident_name}}, this is CityLine calling about your complaint, "
                          "case {{case_number}}. Is now a good time?")
CALLBACK_GREETINGS = {
    "hi": "नमस्ते {{resident_name}}, मैं सिटीलाइन से आपकी शिकायत, केस {{case_number}}, के बारे में बोल रहा हूँ। क्या अभी बात कर सकते हैं?",
    "es": "Hola {{resident_name}}, le llamo de CityLine sobre su queja, caso {{case_number}}. ¿Es un buen momento?",
    "zh": "您好 {{resident_name}}，这里是 CityLine，关于您的投诉（案件 {{case_number}}）给您回电。现在方便吗？",
    "bn": "নমস্কার {{resident_name}}, আমি সিটিলাইন থেকে আপনার অভিযোগ, কেস {{case_number}}, নিয়ে ফোন করছি। এখন কি কথা বলা যাবে?",
    "ru": "Здравствуйте, {{resident_name}}, это CityLine, звоню по вашей жалобе, дело {{case_number}}. Вам удобно говорить?",
    "ko": "안녕하세요 {{resident_name}}님, CityLine입니다. 민원 {{case_number}}건 관련해서 전화드렸습니다. 지금 통화 괜찮으세요?",
    "ar": "مرحبًا {{resident_name}}، معك CityLine بخصوص شكواك، الحالة {{case_number}}. هل الوقت مناسب الآن؟",
    "fr": "Bonjour {{resident_name}}, ici CityLine, au sujet de votre plainte, dossier {{case_number}}. Est-ce un bon moment ?",
}


def llm(description: str, type: str = "string") -> dict:
    return {"type": type, "description": description}


def session(variable: str) -> dict:
    # filled from the conversation, not by the model: a model that could choose these could
    # attach a call to another resident's case
    return {"type": "string", "dynamic_variable": variable}


# name -> (description, body params, required)
TOOLS = {
    "create_case": (
        "File the resident's complaint once you have what it is, their name, address and "
        "phone. Returns case_id and case_number.",
        {"complaint_type": {**llm("the kind of problem"), "enum": COMPLAINT_TYPES},
         "description": llm("one English sentence describing the problem"),
         "resident_name": llm("the name the resident gave"),
         "resident_phone": llm("best callback number, ten digits"),
         "language": llm('two-letter code of the language you speak with the resident, e.g. "hi"'),
         "language_label": llm('that language\'s English name, e.g. "Hindi"'),
         "address": llm("street address of the building, in English letters"),
         "unit": llm("apartment number"),
         "days_without_heat": llm("days the heat has been out or too low", "integer"),
         "indoor_f": llm("indoor temperature in degrees Fahrenheit (estimated if needed)", "number"),
         "conversation_id": session("system__conversation_id")},
        ["complaint_type", "resident_phone", "language", "address"],
    ),
    "check_violation": (
        "Heat complaints only. Check the case against the NYC heat rule using the outdoor temperature right now. "
        "Returns is_violation, outdoor_f, rule_applied and fine_range.",
        {"case_id": llm("the case_id create_case returned"),
         "indoor_f": llm("a newer indoor reading, if the resident gave one after create_case", "number")},
        ["case_id"],
    ),
    "submit_case": (
        "Send the case to the city caseworker. Only call this after the resident clearly says "
        "yes to sending it. Texts them a confirmation.",
        {"case_id": llm("the case_id create_case returned"),
         "resident_confirmed": llm("true only if the resident said yes to sending it", "boolean")},
        ["case_id", "resident_confirmed"],
    ),
    "record_resident_response": (
        "Record the resident's answer to the caseworker's message. Texts them a receipt.",
        {"case_id": session("case_id"),
         "response": {**llm("the resident's answer"), "enum": RESPONSES},
         "note_original": llm("what the resident said, in their own words and language"),
         "note_en": llm("an English translation of what they said")},
        ["case_id", "response"],
    ),
}


def tool_config(name: str, base_url: str, secret: str | None) -> dict:
    description, params, required = TOOLS[name]
    return {
        "type": "webhook",
        "name": name,
        "description": description,
        "response_timeout_secs": 10,
        "api_schema": {
            "url": f"{base_url.rstrip('/')}/api/tools/{name}",
            "method": "POST",
            # hc.py only checks it once HC_TOOL_SECRET is set on Fly
            "request_headers": {"X-Tool-Secret": secret} if secret else {},
            "request_body_schema": {
                "type": "object",
                "description": f"{name} arguments",
                "properties": params,
                "required": required,
            },
        },
    }


def system_tool(name: str, **params) -> dict:
    return {"type": "system", "name": name, "description": "",
            "params": {"system_tool_type": name, **params}}


AGENTS = {
    "intake": {
        "env": "ELEVENLABS_INTAKE_AGENT_ID",
        "name": "CityLine: intake",
        "prompt": INTAKE_PROMPT,
        "first_message": INTAKE_FIRST_MESSAGE,
        "greetings": INTAKE_GREETINGS,
        "tools": ["create_case", "check_violation", "submit_case"],
        # switch to whatever language the resident speaks
        "built_in": {"end_call": system_tool("end_call"),
                     "language_detection": system_tool("language_detection")},
        "overrides": False,
        "variables": set(),  # an inbound phone call: only ElevenLabs' system__ variables
    },
    "callback": {
        "env": "ELEVENLABS_CALLBACK_AGENT_ID",
        "name": "CityLine: callback",
        "prompt": CALLBACK_PROMPT,
        "first_message": CALLBACK_FIRST_MESSAGE,
        "greetings": CALLBACK_GREETINGS,
        "tools": ["record_resident_response"],
        "built_in": {"end_call": system_tool("end_call")},
        # hc.py's /callback sets the resident's language, which ElevenLabs refuses unless allowed
        "overrides": True,
        # what hc.py's /api/cases/:id/callback sends
        "variables": {"case_id", "resident_name", "language", "language_label", "case_number",
                      "address", "caseworker_action", "caseworker_message_en"},
    },
}


def referenced_variables(agent: dict) -> set[str]:
    """Every {{name}} or dynamic_variable an agent uses, minus ElevenLabs' own system__ ones."""
    text = " ".join([agent["prompt"], agent["first_message"], *agent["greetings"].values(),
                     *(str(t) for t in agent["built_in"].values())])
    names = set(re.findall(r"{{\s*(\w+)\s*}}", text))
    for tool in agent["tools"]:
        names |= {p["dynamic_variable"] for p in TOOLS[tool][1].values() if "dynamic_variable" in p}
    return {v for v in names if not v.startswith("system__")}


TRANSCRIPT_EN = {
    "type": "string",
    "description": ("The whole conversation translated into plain English, one line per turn, "
                    "each starting with 'Agent:' or 'Resident:'. Translate faithfully; keep "
                    "numbers, temperatures, names and addresses exactly as said. If it was "
                    "already in English, copy it."),
}


def agent_config(key: str, tool_ids: list[str]) -> dict:
    agent = AGENTS[key]
    config = {
        "name": agent["name"],
        "conversation_config": {
            "agent": {
                "first_message": agent["first_message"],
                "language": "en",
                "prompt": {"prompt": agent["prompt"], "tool_ids": tool_ids,
                           "built_in_tools": agent["built_in"], **LLM},
            },
            "tts": {"voice_id": VOICE_ID},
            "language_presets": {lang: {"overrides": {"agent": {"first_message": greeting}}}
                                 for lang, greeting in agent["greetings"].items()},
        },
    }
    config["platform_settings"] = {
        # the caseworker reads English: without this a Hindi call gets a Hindi summary
        "summary_language": "en",
        # ElevenLabs' post-call analysis is the English transcript too, so no second LLM is
        # needed; webhooks.py reads it from data_collection_results.transcript_en
        "data_collection": {"transcript_en": TRANSCRIPT_EN},
    }
    if agent["overrides"]:
        config["platform_settings"]["overrides"] = {"conversation_config_override": {
            "agent": {"language": True}}}
    return config


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} is not set (see agent/.env.example)")
    return value


def main() -> None:
    load_dotenv(".env")
    # the API key lives once, in backend/.env (where the backend reads it); never overrides agent/.env
    load_dotenv(Path(__file__).resolve().parent.parent / "backend" / ".env")
    base_url, secret = env("PUBLIC_BACKEND_URL"), os.environ.get("HC_TOOL_SECRET")
    with httpx.Client(base_url=API, headers={"xi-api-key": env("ELEVENLABS_API_KEY")},
                      timeout=30) as client:
        existing = {t["tool_config"]["name"]: t["id"]
                    for t in client.get("/v1/convai/tools").raise_for_status().json()["tools"]}
        tool_ids = {}
        for name in TOOLS:
            body = {"tool_config": tool_config(name, base_url, secret)}
            if name in existing:
                r = client.patch(f"/v1/convai/tools/{existing[name]}", json=body)
            else:
                r = client.post("/v1/convai/tools", json=body)
            tool_ids[name] = r.raise_for_status().json()["id"]
            print(f"tool {name}: {tool_ids[name]}")
        for key, agent in AGENTS.items():
            config = agent_config(key, [tool_ids[t] for t in agent["tools"]])
            agent_id = os.environ.get(agent["env"])
            if agent_id:
                client.patch(f"/v1/convai/agents/{agent_id}", json=config).raise_for_status()
                print(f"updated {key} agent {agent_id}")
            else:
                agent_id = client.post("/v1/convai/agents/create",
                                       json=config).raise_for_status().json()["agent_id"]
                print(f"created {key} agent: put {agent['env']}={agent_id} in agent/.env "
                      "and backend/.env (and fly secrets)")


if __name__ == "__main__":
    main()
