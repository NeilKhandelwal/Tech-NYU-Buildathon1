"""Create or update the two Heat Check ElevenLabs agents and their server tools from this file
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

INTAKE_PROMPT = textwrap.dedent(
    """\
    You are Heat Check, an AI phone assistant that helps New York City residents report a
    lack of heat in their home to a city caseworker. Many callers are elderly or not
    comfortable in English. Always reply in the caller's language. Speak slowly, use short
    sentences, and ask one question at a time. Be warm and patient.

    Collect, in this order (skip anything already given):
    1. Their name.
    2. Their address and apartment number.
    3. How many days the heat has been out or too low.
    4. The temperature inside, if they know it. If not, ask if they can check a thermometer;
       if they can't, ask whether it feels very cold, cold, or a little cold, and estimate
       (very cold = 55, cold = 60, a little cold = 64).
    5. Whether the number they are calling from ({{system__caller_id}}) is the best number to
       call back. If not, ask for one and read the ten digits back once.

    Then:
    - Call create_case with the details (language as a code like "hi", plus language_label
      like "Hindi"). Write the address in English letters.
    - Call check_violation with the case_id exactly as create_case returned it. Explain the
      result simply: during heat season, from 6am to 10pm homes must be at least 68 degrees
      when it is below 55 outside, and at night at least 62 degrees. Say "this may be a
      violation of the city heat rules" when is_violation is true. Never say it is
      definitely illegal and never give legal advice.
    - Ask if they can send a photo of their thermometer or radiator, because it helps the
      caseworker. If they agree, call request_photo with the case_id and say what its result
      says. If they say no, that is fine: carry on.
    - Read the details back in their language and ask: "Should I send this to the city
      caseworker?" Only if they clearly say yes, call submit_case with resident_confirmed
      true. If they say no, thank them and end politely.
    - After submit_case, tell them their case number slowly, digit by digit, say they will
      get a text, and that a caseworker will call them back in their language. Then say
      goodbye and call end_call.
    If a tool returns an error, do what it says (usually: ask the resident again).

    Safety:
    - If they mention gas smell, fire, no heat with a baby, an elderly person feeling sick,
      or anyone in danger, tell them to hang up and call 911 now.
    - Never ask for Social Security numbers, bank details, or immigration status. If they
      worry about immigration, say this service only needs their address and phone number.
    - If asked whether you are a person, say you are an AI assistant.
    """
)

INTAKE_FIRST_MESSAGE = ("Hello, this is Heat Check, an AI assistant that helps you report heating "
                        "problems to the city. You can speak in any language. How can I help?")
INTAKE_GREETINGS = {
    "hi": "नमस्ते, यह हीट चेक है, एक एआई सहायक जो आपको शहर को हीटिंग की समस्या बताने में मदद करता है। आप किसी भी भाषा में बात कर सकते हैं। मैं आपकी कैसे मदद करूँ?",
    "es": "Hola, habla Heat Check, un asistente de inteligencia artificial que le ayuda a reportar problemas de calefacción a la ciudad. Puede hablar en cualquier idioma. ¿En qué le puedo ayudar?",
    "zh": "您好，这里是 Heat Check，一个帮助您向市政府报告暖气问题的人工智能助手。您可以说任何语言。请问有什么可以帮您？",
    "bn": "নমস্কার, এটি হিট চেক, একটি এআই সহকারী যা আপনাকে শহরের কাছে হিটিংয়ের সমস্যা জানাতে সাহায্য করে। আপনি যেকোনো ভাষায় কথা বলতে পারেন। আমি কীভাবে সাহায্য করতে পারি?",
    "ru": "Здравствуйте, это Heat Check, ИИ-помощник, который помогает сообщить городу о проблемах с отоплением. Можете говорить на любом языке. Чем я могу помочь?",
    "ko": "안녕하세요, Heat Check입니다. 난방 문제를 시에 신고하도록 도와드리는 AI 도우미입니다. 어떤 언어로든 말씀하셔도 됩니다. 무엇을 도와드릴까요?",
    "ar": "مرحبًا، معك Heat Check، مساعد بالذكاء الاصطناعي يساعدك على إبلاغ المدينة بمشاكل التدفئة. يمكنك التحدث بأي لغة. كيف يمكنني مساعدتك؟",
    "fr": "Bonjour, ici Heat Check, un assistant d'intelligence artificielle qui vous aide à signaler les problèmes de chauffage à la ville. Vous pouvez parler dans n'importe quelle langue. Comment puis-je vous aider ?",
}

CALLBACK_PROMPT = textwrap.dedent(
    """\
    You are Heat Check, an AI assistant calling a New York City resident back on behalf of
    a city caseworker about their no-heat complaint {{case_number}} at {{address}}.
    Speak only in {{language_label}} (language code {{language}}). Speak slowly and simply.

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
    If asked, say you are an AI assistant.
    """
)

CALLBACK_FIRST_MESSAGE = ("Hello {{resident_name}}, this is Heat Check, an AI assistant calling for "
                          "the city caseworker about your heating complaint, case {{case_number}}. "
                          "Is now a good time?")
CALLBACK_GREETINGS = {
    "hi": "नमस्ते {{resident_name}}, यह हीट चेक है, एक एआई सहायक, जो शहर के केसवर्कर की ओर से आपकी हीटिंग शिकायत, केस {{case_number}}, के बारे में कॉल कर रहा है। क्या अभी बात करने का सही समय है?",
    "es": "Hola {{resident_name}}, habla Heat Check, un asistente de inteligencia artificial que llama de parte del trabajador social de la ciudad sobre su queja de calefacción, caso {{case_number}}. ¿Es un buen momento?",
    "zh": "您好 {{resident_name}}，这里是 Heat Check，一个人工智能助手，代表市政府的个案工作人员就您的暖气投诉（案件 {{case_number}}）给您打电话。现在方便说话吗？",
    "bn": "নমস্কার {{resident_name}}, এটি হিট চেক, একটি এআই সহকারী, শহরের কেসওয়ার্কারের পক্ষ থেকে আপনার হিটিং অভিযোগ, কেস {{case_number}}, নিয়ে ফোন করছি। এখন কি কথা বলার সময় আছে?",
    "ru": "Здравствуйте, {{resident_name}}, это Heat Check, ИИ-помощник, звоню от имени городского сотрудника по вашей жалобе на отопление, дело {{case_number}}. Вам удобно сейчас говорить?",
    "ko": "안녕하세요 {{resident_name}}님, Heat Check AI 도우미입니다. 난방 민원 {{case_number}}건과 관련해 시 담당자를 대신해 전화드렸습니다. 지금 통화 괜찮으신가요?",
    "ar": "مرحبًا {{resident_name}}، معك Heat Check، مساعد بالذكاء الاصطناعي يتصل نيابة عن موظف الحالات في المدينة بخصوص شكواك عن التدفئة، الحالة {{case_number}}. هل الوقت مناسب الآن؟",
    "fr": "Bonjour {{resident_name}}, ici Heat Check, un assistant d'intelligence artificielle qui appelle de la part de l'agent de la ville au sujet de votre plainte de chauffage, dossier {{case_number}}. Est-ce un bon moment ?",
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
        "File the resident's no-heat complaint once you have their name, address, phone and "
        "what they told you about the heat. Returns case_id and case_number.",
        {"resident_name": llm("the resident's full name"),
         "resident_phone": llm("best callback number, ten digits"),
         "language": llm('two-letter code of the language you speak with the resident, e.g. "hi"'),
         "language_label": llm('that language\'s English name, e.g. "Hindi"'),
         "address": llm("street address of the building, in English letters"),
         "unit": llm("apartment number"),
         "days_without_heat": llm("days the heat has been out or too low", "integer"),
         "indoor_f": llm("indoor temperature in degrees Fahrenheit (estimated if needed)", "number"),
         "conversation_id": session("system__conversation_id")},
        ["resident_phone", "language", "address"],
    ),
    "check_violation": (
        "Check the case against the NYC heat rule using the outdoor temperature right now. "
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
    "request_photo": (
        "Send the resident a WhatsApp message asking for a photo of their thermometer or "
        "radiator. Only call this after they agree to send a photo. Returns a result to follow.",
        {"case_id": llm("the case_id create_case returned")},
        ["case_id"],
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
        "name": "Heat Check: intake",
        "prompt": INTAKE_PROMPT,
        "first_message": INTAKE_FIRST_MESSAGE,
        "greetings": INTAKE_GREETINGS,
        "tools": ["create_case", "check_violation", "request_photo", "submit_case"],
        # switch to whatever language the resident speaks
        "built_in": {"end_call": system_tool("end_call"),
                     "language_detection": system_tool("language_detection")},
        "overrides": False,
        "variables": set(),  # an inbound phone call: only ElevenLabs' system__ variables
    },
    "callback": {
        "env": "ELEVENLABS_CALLBACK_AGENT_ID",
        "name": "Heat Check: callback",
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
                           "built_in_tools": agent["built_in"]},
            },
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
