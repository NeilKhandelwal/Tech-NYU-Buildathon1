import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import provision  # noqa: E402

HC = Path(__file__).resolve().parents[2] / "backend" / "app" / "hc.py"


def test_every_tool_has_a_backend_route():
    """A renamed tool or route would 404 mid-call, and the agent would just say it failed."""
    source = HC.read_text()
    for name in provision.TOOLS:
        schema = provision.tool_config(name, "https://x/", None)["api_schema"]
        assert schema["url"] == f"https://x/api/tools/{name}"
        assert f'@app.post("/api/tools/{name}"' in source, name


def test_tools_send_the_secret_hc_checks_only_when_one_is_set():
    """hc.py compares X-Tool-Secret against HC_TOOL_SECRET; an empty header when it is unset
    keeps the tools working before anyone sets it."""
    assert provision.tool_config("create_case", "https://x", "s")["api_schema"]["request_headers"] == {"X-Tool-Secret": "s"}
    assert provision.tool_config("create_case", "https://x", None)["api_schema"]["request_headers"] == {}
    assert "x_tool_secret" in HC.read_text()


def test_body_fields_are_ones_the_route_accepts():
    """Pydantic drops unknown fields silently, so a misspelled field would just never arrive."""
    source = HC.read_text()
    for name, (_, params, _) in provision.TOOLS.items():
        for field in params:
            assert re.search(rf"^\s+{field}: ", source, re.M), (name, field)


def test_create_case_carries_the_conversation_id_from_the_session():
    """The post-call webhook finds the case by this id; if the model filled it, it would guess."""
    params = provision.TOOLS["create_case"][1]
    assert params["conversation_id"] == {"type": "string", "dynamic_variable": "system__conversation_id"}


def test_callback_case_id_comes_from_the_dial_not_the_model():
    """The callback agent must answer for the case the caseworker clicked, not one it chose."""
    params = provision.TOOLS["record_resident_response"][1]
    assert params["case_id"] == {"type": "string", "dynamic_variable": "case_id"}


def test_submit_needs_the_residents_yes():
    """The resident's spoken yes is the human check on the intake side."""
    _, params, required = provision.TOOLS["submit_case"]
    assert params["resident_confirmed"]["type"] == "boolean"
    assert "resident_confirmed" in required
    assert "Only if they clearly say yes" in provision.INTAKE_PROMPT


def test_response_enum_matches_the_backend():
    """hc.py refuses anything outside RESPONSES; drift means a refused answer on every call."""
    body = HC.read_text().split("RESPONSES = {", 1)[1].split("}", 1)[0]
    assert provision.RESPONSES == re.findall(r'"(\w+)":', body)


def test_agents_only_name_variables_their_calls_send():
    """ElevenLabs refuses to start a conversation missing a variable the prompt, greeting or
    tools name, so a typo would fail every call to that agent."""
    for key, agent in provision.AGENTS.items():
        assert provision.referenced_variables(agent) <= agent["variables"], key


def test_callback_variables_are_what_the_backend_dials_with():
    source = HC.read_text().split("async def callback", 1)[1]
    for var in provision.AGENTS["callback"]["variables"]:
        assert f'"{var}"' in source, var


def test_only_the_callback_agent_allows_a_language_override():
    """/callback sets the resident's language; ElevenLabs refuses the override unless allowed."""
    callback = provision.agent_config("callback", [])
    assert callback["platform_settings"]["overrides"]["conversation_config_override"]["agent"]["language"]
    assert "platform_settings" not in provision.agent_config("intake", [])


def test_intake_detects_the_callers_language():
    built_in = provision.agent_config("intake", [])["conversation_config"]["agent"]["prompt"]["built_in_tools"]
    assert "language_detection" in built_in


def test_greetings_cover_the_same_languages():
    for agent in provision.AGENTS.values():
        assert set(agent["greetings"]) == set(provision.LANGUAGES), agent["name"]


def test_every_greeting_keeps_the_callback_variables():
    """A translated greeting that drops {{case_number}} would read a different line per language."""
    for lang, text in provision.CALLBACK_GREETINGS.items():
        assert "{{resident_name}}" in text and "{{case_number}}" in text, lang
