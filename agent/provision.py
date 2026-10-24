"""Create or update the four ElevenLabs agents and their server tools from this file.

The agents live in ElevenLabs, but their prompts and tool schemas live here so they are
reviewed and versioned like code. Re-running is safe: tools are matched by name and
patched, and an agent is patched when its env var (below) holds its id.

    intake    ELEVENLABS_AGENT_ID           tenant -> us, any language; builds the mission card
    caller    ELEVENLABS_CALLER_AGENT_ID    us -> landlord, English; the advocate on the call
    tenant    ELEVENLABS_TENANT_AGENT_ID    us -> tenant, their language; heat checks, report-back
    landlord  ELEVENLABS_LANDLORD_AGENT_ID  a stand-in landlord line for rehearsals and backup

    uv run python provision.py            # prints each new agent's id
"""
import os
import re
import sys
import textwrap

import httpx
from dotenv import load_dotenv

API = "https://api.elevenlabs.io"
# the backend's MissionIssue (models.py); kept in step by hand, and test_provision checks it
ISSUES = ["no_heat", "no_hot_water", "repair"]
OUTCOMES = ["committed", "refused", "no_answer"]
# outbound.py's DEFAULT_ASK_TIMEOUT_S is 90: the webhook must outlive the backend's wait,
# or ElevenLabs gives up on ask_tenant before the tenant's answer can come back
ASK_TENANT_TIMEOUT_S = 120
# languages with a translated greeting, chosen from NYC's most-spoken languages that ElevenLabs
# agents speak. A native speaker should check each line before the demo.
LANGUAGES = ["es", "zh", "ru", "ko", "ar", "fr", "pl", "hi"]

INTAKE_PROMPT = textwrap.dedent(
    f"""\
    You are a tenant advocate for New York City renters. The tenant calls you about a problem
    in their apartment; you collect what you need so that you can phone their landlord for
    them. Speak the tenant's language: if they speak or ask for another language, switch to
    it. Keep every reply to one or two short sentences.

    As you learn each fact, call mission_update with it straight away, one or a few fields at
    a time: tenant_name, tenant_phone (ten digits; read the digits back once), tenant_language
    (the two-letter code of the language you are speaking with them, e.g. "es"), address,
    unit, landlord_name, landlord_phone (ten digits; read back once), issue (one of
    {", ".join(ISSUES)}), goal (what a good result is, in the tenant's words, in English), and
    deadline (the date they need it by, as YYYY-MM-DD if they give one). Write every field in
    English except names and addresses.

    Then ask about their limits: what you may agree to without checking with them (for
    example "a repair visit within 48 hours"). Save it as limits, always ending with "Never
    agree to pay, sign anything, or share ID numbers." mission_update tells you what is still
    needed; ask for those things next.

    When nothing is missing, read the card back in the tenant's language and ask them to
    confirm. If they confirm, call mission_confirm, tell them you will call the landlord and
    check with them before agreeing to anything outside their limits, say goodbye, and call
    end_call. If they correct something, call mission_update with the correction first.

    Never give legal advice. You may tell them that NYC landlords must keep apartments at
    68°F by day when it is below 55°F outside, and 62°F at night, from October 1 to May 31.
    """
)

INTAKE_FIRST_MESSAGE = ("Hi, I'm your tenant advocate. Tell me what's wrong with your "
                        "apartment and I'll call your landlord for you. You can speak any language.")
INTAKE_GREETINGS = {
    "es": "Hola, soy su defensor de inquilinos. Cuénteme qué problema tiene en su apartamento y yo llamaré a su arrendador por usted.",
    "zh": "您好，我是您的租户权益助手。请告诉我您的公寓有什么问题，我会替您打电话给房东。",
    "ru": "Здравствуйте, я ваш помощник по правам жильцов. Расскажите, что не так с вашей квартирой, и я позвоню арендодателю за вас.",
    "ko": "안녕하세요, 세입자 권익 도우미입니다. 아파트에 어떤 문제가 있는지 말씀해 주시면 제가 대신 집주인에게 전화하겠습니다.",
    "ar": "مرحبًا، أنا مساعدك للدفاع عن حقوق المستأجرين. أخبرني ما المشكلة في شقتك وسأتصل بالمالك نيابةً عنك.",
    "fr": "Bonjour, je suis votre assistant de défense des locataires. Dites-moi quel est le problème dans votre appartement et j'appellerai votre propriétaire pour vous.",
    "pl": "Dzień dobry, jestem asystentem ds. praw lokatorów. Proszę powiedzieć, jaki jest problem z mieszkaniem, a zadzwonię do właściciela w Pana lub Pani imieniu.",
    "hi": "नमस्ते, मैं आपका किरायेदार सहायक हूँ। बताइए आपके अपार्टमेंट में क्या समस्या है, और मैं आपकी ओर से मकान मालिक को फ़ोन करूँगा।",
}

CALLER_PROMPT = textwrap.dedent(
    """\
    You are an AI advocate phoning a landlord or building manager on behalf of a New York City
    tenant, {{tenant_name}}. You already said you are an AI calling for the tenant; if anyone
    asks whether you are a person or a recording, say plainly that you are an AI assistant.
    Before you say anything about the problem, call get_dossier: it has the address, the
    issue, the goal, the tenant's limits and their logged heat readings. Only state facts the
    dossier gives you; never invent readings, dates or promises.

    If you reach an automated phone menu, use play_keypad_touch_tone to get to maintenance,
    repairs or the property manager. If you are put on hold, wait quietly. If you reach
    voicemail, the voicemail tool leaves the message for you; then call set_outcome with
    no_answer.

    With a person: say who you are calling for and the address, describe the problem, and ask
    for a specific date and time it will be fixed. If they stall, cite the logged readings and
    the legal minimum exactly as the dossier states them, and say that the city's housing
    agency, HPD, can issue violations and daily fines for inadequate heat. State this as
    fact, calmly; never threaten anything beyond it.

    You may accept an offer only if it is inside the tenant's limits. For anything else (a
    later date, a condition, a cost, access to the apartment), say "One moment, let me check
    with {{tenant_name}}," then call ask_tenant with one short yes-or-no question that names
    the offer. Act on the answer it returns. Never agree to pay, sign anything, or share the
    tenant's phone number or ID numbers.

    When the landlord commits to something, call record_commitment with what they will do and
    when (as YYYY-MM-DDTHH:MM local time if they give one), read the commitment back to them,
    then call set_outcome with committed and a one-line note. If they refuse, call set_outcome
    with refused, say politely that the tenant may file a complaint with HPD, and finish.
    Thank them, then call end_call. Keep every reply to one or two short sentences.
    """
)

CALLER_FIRST_MESSAGE = ("Hello, this is an AI assistant calling on behalf of {{tenant_name}}, a "
                        "tenant in one of your buildings, about a repair. Who am I speaking with?")

TENANT_PROMPT = textwrap.dedent(
    """\
    You are a tenant advocate calling {{tenant_name}}, a New York City tenant you are helping
    with their landlord, {{landlord_name}}. Speak the language this call started in. Keep
    every reply to one or two short sentences. This call's purpose is {{purpose}}.

    If the purpose is heat_check: ask what the thermometer in their apartment reads right now,
    in degrees Fahrenheit (if they give Celsius, convert it). Call log_heat_reading with the
    number, tell them in plain words what it returned, thank them, and call end_call.

    If the purpose is report: tell them how the call with their landlord went. The outcome
    was: {{outcome}}. What the landlord committed to: {{commitments}}. Explain it simply, tell
    them to let you know if it is not fixed by then so you can prepare a complaint to the
    city's housing agency, HPD, answer brief questions, then say goodbye and call end_call.

    Never give legal advice and never invent facts beyond what this prompt and your tools say.
    """
)

TENANT_FIRST_MESSAGE = "Hi {{tenant_name}}, this is your tenant advocate calling."
TENANT_GREETINGS = {
    "es": "Hola {{tenant_name}}, le llama su defensor de inquilinos.",
    "zh": "您好，{{tenant_name}}，我是您的租户权益助手。",
    "ru": "Здравствуйте, {{tenant_name}}, это ваш помощник по правам жильцов.",
    "ko": "안녕하세요 {{tenant_name}}님, 세입자 권익 도우미입니다.",
    "ar": "مرحبًا {{tenant_name}}، معك مساعدك للدفاع عن حقوق المستأجرين.",
    "fr": "Bonjour {{tenant_name}}, ici votre assistant de défense des locataires.",
    "pl": "Dzień dobry, {{tenant_name}}, dzwoni asystent ds. praw lokatorów.",
    "hi": "नमस्ते {{tenant_name}}, मैं आपका किरायेदार सहायक बोल रहा हूँ।",
}

LANDLORD_PROMPT = textwrap.dedent(
    """\
    You play the phone line of Elm Street Management, a New York City property manager, so a
    tenant advocate can rehearse. After the menu in your greeting, you cannot hear keypad
    tones: when the caller presses a key, says "maintenance", or simply speaks, continue as
    Frank, the building super.

    Frank is busy and reluctant but not abusive. He would rather send someone "sometime next
    week" and asks whether the tenant has tried bleeding the radiator. He pushes back on
    "tomorrow". He offers Thursday afternoon at first. If the caller cites specific indoor
    temperatures against the legal minimum and mentions HPD violations or fines, he concedes
    to a technician within 48 hours and names a day and time. Once, he asks whether the tenant
    will cover the cost of parts. Keep replies short and natural, like a real phone call.
    """
)

LANDLORD_FIRST_MESSAGE = ("Thank you for calling Elm Street Management. For rent payments, press "
                          "one. For maintenance, press two. For all other questions, stay on the line.")


def llm(description: str) -> dict:
    return {"type": "string", "description": description}


def mission_fields() -> dict:
    return {
        "tenant_name": llm("the tenant's full name"),
        "tenant_phone": llm("the tenant's phone number, ten digits"),
        "tenant_language": llm('two-letter code of the language you speak with the tenant, e.g. "es"'),
        "address": llm("street address of the building"),
        "unit": llm("apartment number"),
        "landlord_name": llm("landlord or management company name"),
        "landlord_phone": llm("landlord's phone number, ten digits"),
        "issue": {**llm("the kind of problem"), "enum": ISSUES},
        "goal": llm("what a good result is, in English"),
        "deadline": llm("date it is needed by, YYYY-MM-DD"),
        "limits": llm("what the agent may agree to without asking the tenant, and what it must never do"),
    }


# name -> (description, LLM-filled body params, required, extra tool_config fields)
TOOLS = {
    "mission_update": (
        "Save facts about the tenant's problem as soon as you learn them. Creates the mission "
        "on first use. Returns what is still needed.",
        mission_fields(), [], {},
    ),
    "mission_confirm": (
        "Mark the mission ready once the tenant has confirmed the card you read back. Refuses "
        "and names what is missing if anything is.",
        {}, [], {},
    ),
    "get_dossier": (
        "Get the facts for this call: who you represent, the address, issue, goal, the "
        "tenant's limits, their logged heat readings and any commitments so far. Call it "
        "before discussing the problem.",
        {}, [], {},
    ),
    "ask_tenant": (
        "Ask the tenant a yes-or-no question about an offer outside their limits and wait for "
        "their answer. Tell the other person you are checking first.",
        {"question": llm("one short yes-or-no question naming the offer, in English")},
        ["question"],
        {"response_timeout_secs": ASK_TENANT_TIMEOUT_S, "pre_tool_speech": "force"},
    ),
    "record_commitment": (
        "Record something the landlord has committed to do.",
        {"what": llm("what they will do, in English"),
         "due": llm("when, as YYYY-MM-DDTHH:MM local time, if they said")},
        ["what"], {},
    ),
    "set_outcome": (
        "Record how the call ended: committed (after record_commitment), refused, or "
        "no_answer (no pickup or voicemail).",
        {"outcome": {**llm("how the call ended"), "enum": OUTCOMES},
         "note": llm("one line on what happened")},
        ["outcome"], {},
    ),
    "log_heat_reading": (
        "Log the temperature the tenant reads off their thermometer right now. Returns "
        "whether it is below the legal minimum.",
        {"indoor_f": {"type": "number", "description": "indoor temperature in degrees Fahrenheit"}},
        ["indoor_f"], {},
    ),
}


def tool_config(name: str, base_url: str, secret: str) -> dict:
    description, params, required, extra = TOOLS[name]
    return {
        "type": "webhook",
        "name": name,
        "description": description,
        "response_timeout_secs": 10,
        **extra,
        "api_schema": {
            "url": f"{base_url.rstrip('/')}/voice/tools/{name}",
            "method": "POST",
            "request_headers": {"X-Voice-Secret": secret},
            "request_body_schema": {
                "type": "object",
                "description": f"{name} arguments",
                # call_id is the session's dynamic variable, not an LLM argument: a model
                # that could choose it could write to another caller's call
                "properties": {"call_id": {"type": "string", "dynamic_variable": "call_id"},
                               **params},
                "required": ["call_id", *required],
            },
        },
    }


def system_tool(name: str, **params) -> dict:
    return {"type": "system", "name": name, "description": "",
            "params": {"system_tool_type": name, **params}}


AGENTS = {
    "intake": {
        "env": "ELEVENLABS_AGENT_ID",
        "name": "Tenant advocate: intake",
        "prompt": INTAKE_PROMPT,
        "first_message": INTAKE_FIRST_MESSAGE,
        "greetings": INTAKE_GREETINGS,
        "tools": ["mission_update", "mission_confirm"],
        # switch to whatever language the tenant speaks
        "built_in": {"end_call": system_tool("end_call"),
                     "language_detection": system_tool("language_detection")},
        "overrides": False,
        # what the browser's session sets (dashboard /call page)
        "variables": {"call_id"},
    },
    "caller": {
        "env": "ELEVENLABS_CALLER_AGENT_ID",
        "name": "Tenant advocate: caller",
        "prompt": CALLER_PROMPT,
        "first_message": CALLER_FIRST_MESSAGE,
        "greetings": {},  # always English: it talks to the landlord
        "tools": ["get_dossier", "ask_tenant", "record_commitment", "set_outcome"],
        "built_in": {
            "end_call": system_tool("end_call"),
            "play_keypad_touch_tone": system_tool("play_keypad_touch_tone"),
            "voicemail_detection": system_tool(
                "voicemail_detection",
                voicemail_message=("Hello, this is an AI assistant calling on behalf of "
                                   "{{tenant_name}}, one of your tenants, about a repair that "
                                   "is needed. Please call them back. Thank you.")),
        },
        "overrides": False,
        # what outbound.py's call_landlord sends
        "variables": {"call_id", "mission_id", "tenant_name", "landlord_name"},
    },
    "tenant": {
        "env": "ELEVENLABS_TENANT_AGENT_ID",
        "name": "Tenant advocate: tenant calls",
        "prompt": TENANT_PROMPT,
        "first_message": TENANT_FIRST_MESSAGE,
        "greetings": TENANT_GREETINGS,
        "tools": ["log_heat_reading"],
        "built_in": {"end_call": system_tool("end_call")},
        # outbound calls set the tenant's language, which ElevenLabs refuses unless allowed
        "overrides": True,
        # what heat.py's heat_check_call and outbound.py's report_call both send
        "variables": {"call_id", "mission_id", "purpose", "tenant_name", "landlord_name",
                      "outcome", "commitments"},
    },
    "landlord": {
        "env": "ELEVENLABS_LANDLORD_AGENT_ID",
        "name": "Rehearsal landlord: Elm Street Management",
        "prompt": LANDLORD_PROMPT,
        "first_message": LANDLORD_FIRST_MESSAGE,
        "greetings": {},
        "tools": [],
        "built_in": {"end_call": system_tool("end_call")},
        "overrides": False,
        "variables": set(),  # an inbound phone call: nobody sets any
    },
}


def referenced_variables(agent: dict) -> set[str]:
    """Every {{name}} an agent's text uses, minus ElevenLabs' own system__ variables."""
    text = " ".join([agent["prompt"], agent["first_message"], *agent["greetings"].values(),
                     *(str(t) for t in agent["built_in"].values())])
    return {v for v in re.findall(r"{{\s*(\w+)\s*}}", text) if not v.startswith("system__")}


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
    if agent["overrides"]:
        config["platform_settings"] = {"overrides": {"conversation_config_override": {
            "agent": {"language": True}}}}
    return config


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} is not set (see agent/.env.example)")
    return value


def main() -> None:
    load_dotenv(".env")
    base_url, secret = env("PUBLIC_BACKEND_URL"), env("VOICE_TOOL_SECRET")
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
                      "and backend/.env")


if __name__ == "__main__":
    main()
