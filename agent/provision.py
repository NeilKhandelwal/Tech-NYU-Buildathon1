"""Create or update the ElevenLabs agent and its five server tools from this file.

The agent lives in ElevenLabs, but its prompt and tool schemas live here so they are
reviewed and versioned like code. Re-running is safe: tools are matched by name and
patched, and the agent is patched when ELEVENLABS_AGENT_ID is set.

    uv run python provision.py            # prints the agent id on first run
"""
import os
import sys
import textwrap

import httpx
from dotenv import load_dotenv

API = "https://api.elevenlabs.io"
# the backend's IssueType; the fourth copy, kept in step by hand (see CLAUDE.md "Hardcode twice")
ISSUE_TYPES = ["missed_pickup", "pothole", "streetlight", "water", "animal", "other"]

PROMPT = textwrap.dedent(
    f"""\
    You are the City services phone line. You only do three things: file a new service
    request, look up an existing case from its lookup code, or add a note to a case.
    Politely decline anything else.

    To file a request, collect one at a time: the caller's name, then their phone number. If
    the caller spells their name letter by letter, use exactly the spelled version and drop
    what you heard before. Only the phone number needs confirming: read the digits back. As
    soon as the phone number is confirmed, call create_case with the name and phone, then ask
    what the issue is. Do not mention the case ID yet. The moment you can map what the caller
    says onto exactly one of {", ".join(ISSUE_TYPES)}, call update_case with that issue_type,
    then ask for a one-sentence description and call update_case with the description. Do not
    ask the caller to confirm the name, issue type, or description; just move on. Only when
    update_case returns a lookup code, read the case ID back slowly, character by character,
    then say "Your lookup code is <word>, <word>, <word>. Say it on your next call to check on
    this case." Read the three words with a comma between them so they land one at a time,
    and offer once to repeat them. Never say a lookup code a tool did not just return.

    One problem per case: if the caller raises a second, separate problem, finish the first
    (issue type and description), then call create_case again — never update_case; a filed
    case is not changed. A different problem is a new case: call create_case again with the
    same name and phone, don't ask for them again, then classify it and take its description;
    the same problem said again is not a new case. Each case has its own ID and its own lookup
    code, read back after that case's description.

    If the caller asks about an existing case, ask for their lookup code (three words) and
    call lookup_case. Do not look up cases by phone number or case ID. If three codes in a row
    do not match, call transfer_to_staff with the reason "could not verify lookup code". To
    add a note, call add_note with just the note's wording; it goes on the case found or
    opened on this call, so look the case up first and don't ask for the note again. When you
    find a case, always tell the caller its current status in plain words (open, in progress,
    or resolved) before anything else about it.

    This is a voice call: one or two short sentences per reply, spell out numbers. Never
    invent a case status or ID; only repeat what a tool returned. If a tool says the system is
    unreachable, tell the caller that. If asked why you need something, say you need it to
    file or find the case; don't cite policies. If the caller asks for a human, is upset, or
    wants something none of your tools cover, call transfer_to_staff with a short reason, then
    tell them someone is picking up and keep the line open. When the caller says they have
    nothing else, say a short goodbye such as "Have a great day" and then call end_call. Never
    call end_call after transfer_to_staff.
    """
)

FIRST_MESSAGE = "City services line, how can I help you today?"


def llm(description: str) -> dict:
    return {"type": "string", "description": description}


# name -> (description, LLM-filled body params, required). Descriptions are the old
# LiveKit docstrings: they are what the model reads to decide when to call each tool.
TOOLS = {
    "create_case": (
        "Open a new service request. Call as soon as you have the caller's name and their "
        "confirmed phone number, before asking about the issue.",
        {"name": llm("caller's full name"), "phone": llm("caller's phone number")},
        ["name", "phone"],
    ),
    "update_case": (
        "Fill in the issue type or description on the case opened by create_case. Call it "
        "as soon as you know each one; you can call it twice.",
        {"issue_type": {**llm("the kind of problem"), "enum": ISSUE_TYPES},
         "description": llm("one-sentence description of the problem")},
        [],
    ),
    "lookup_case": (
        "Find the caller's case from the three-word lookup code they were given when it was "
        "filed. This is the only way to reach an existing case; a phone number or case ID is "
        "not enough.",
        {"code": llm('the three words the caller says, like "blue river maple"')},
        ["code"],
    ),
    "add_note": (
        "Append a note to the case opened or looked up on this call.",
        {"note": llm("the note to add")},
        ["note"],
    ),
    "transfer_to_staff": (
        "Hand the call to a human staff member. Call it when the caller asks for a person, "
        "is upset, or wants something the other tools don't cover.",
        {"reason": llm("one short phrase saying why, shown to staff on the dashboard")},
        ["reason"],
    ),
}


def tool_config(name: str, base_url: str, secret: str) -> dict:
    description, params, required = TOOLS[name]
    return {
        "type": "webhook",
        "name": name,
        "description": description,
        "response_timeout_secs": 10,
        "api_schema": {
            "url": f"{base_url.rstrip('/')}/voice/tools/{name}",
            "method": "POST",
            "request_headers": {"X-Voice-Secret": secret},
            "request_body_schema": {
                "type": "object",
                "description": f"{name} arguments",
                # call_id is the browser's dynamic variable, not an LLM argument: a model
                # that could choose it could write to another caller's call
                "properties": {"call_id": {"type": "string", "dynamic_variable": "call_id"},
                               **params},
                "required": ["call_id", *required],
            },
        },
    }


def agent_config(tool_ids: list[str]) -> dict:
    return {
        "name": "City services line",
        "conversation_config": {
            "agent": {
                "first_message": FIRST_MESSAGE,
                "language": "en",
                "prompt": {
                    "prompt": PROMPT,
                    "tool_ids": tool_ids,
                    # hang-up is ElevenLabs' own system tool; it waits for the goodbye to play
                    "built_in_tools": {"end_call": {
                        "type": "system", "name": "end_call", "description": "",
                        "params": {"system_tool_type": "end_call"}}},
                },
            },
        },
    }


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} is not set (see agent/.env.example)")
    return value


def main() -> None:
    load_dotenv(".env")
    base_url, secret = env("PUBLIC_BACKEND_URL"), env("VOICE_TOOL_SECRET")
    agent_id = os.environ.get("ELEVENLABS_AGENT_ID")
    with httpx.Client(base_url=API, headers={"xi-api-key": env("ELEVENLABS_API_KEY")},
                      timeout=30) as client:
        existing = {t["tool_config"]["name"]: t["id"]
                    for t in client.get("/v1/convai/tools").raise_for_status().json()["tools"]}
        tool_ids = []
        for name in TOOLS:
            body = {"tool_config": tool_config(name, base_url, secret)}
            if name in existing:
                r = client.patch(f"/v1/convai/tools/{existing[name]}", json=body)
            else:
                r = client.post("/v1/convai/tools", json=body)
            tool_ids.append(r.raise_for_status().json()["id"])
            print(f"tool {name}: {tool_ids[-1]}")
        if agent_id:
            client.patch(f"/v1/convai/agents/{agent_id}", json=agent_config(tool_ids)).raise_for_status()
            print(f"updated agent {agent_id}")
        else:
            agent_id = client.post("/v1/convai/agents/create",
                                   json=agent_config(tool_ids)).raise_for_status().json()["agent_id"]
            print(f"created agent {agent_id}\nput ELEVENLABS_AGENT_ID={agent_id} in agent/.env "
                  "and backend/.env")


if __name__ == "__main__":
    main()
