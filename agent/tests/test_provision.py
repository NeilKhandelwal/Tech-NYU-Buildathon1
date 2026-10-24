import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import provision  # noqa: E402

APP = Path(__file__).resolve().parents[2] / "backend" / "app"


def backend_source() -> str:
    return "\n".join(p.read_text() for p in APP.glob("*.py"))


def test_every_tool_takes_call_id_from_the_session_not_the_llm():
    """If the model could choose call_id, one caller's words could write to another's call."""
    for name in provision.TOOLS:
        body = provision.tool_config(name, "https://x", "s")["api_schema"]["request_body_schema"]
        assert body["properties"]["call_id"] == {"type": "string", "dynamic_variable": "call_id"}
        assert "call_id" in body["required"]


def test_every_tool_has_a_backend_route_and_sends_the_secret():
    """A renamed tool or route would 404 mid-call, and the agent would just say it failed."""
    source = backend_source()
    for name in provision.TOOLS:
        schema = provision.tool_config(name, "https://x/", "s")["api_schema"]
        assert schema["url"] == f"https://x/voice/tools/{name}"
        assert re.search(rf'\.post\("/voice/tools/{name}"\)', source), name
        assert schema["request_headers"] == {"X-Voice-Secret": "s"}


def test_every_agent_tool_is_defined():
    for key, agent in provision.AGENTS.items():
        assert set(agent["tools"]) <= set(provision.TOOLS), key


def test_agents_only_name_variables_their_calls_send():
    """ElevenLabs refuses to start a conversation that is missing a variable the prompt,
    greeting or tool text names, so a typo or a variable the backend never sends would fail
    every call to that agent."""
    for key, agent in provision.AGENTS.items():
        assert provision.referenced_variables(agent) <= agent["variables"], key


def test_declared_variables_are_ones_the_backend_actually_sends():
    """The other half: the declared set must match what heat.py and outbound.py dial with."""
    source = backend_source()
    for key, agent in provision.AGENTS.items():
        for var in agent["variables"] - {"call_id"}:
            assert f'"{var}"' in source, (key, var)


def test_ask_tenant_outlives_the_backend_wait():
    """The backend waits up to 90 s for the tenant; a shorter webhook timeout would cut the
    answer off and the agent would act as if the tenant never replied."""
    outbound = (APP / "outbound.py").read_text()
    backend_wait = float(re.search(r"DEFAULT_ASK_TIMEOUT_S\s*=\s*([\d.]+)", outbound).group(1))
    config = provision.tool_config("ask_tenant", "https://x", "s")
    assert config["response_timeout_secs"] > backend_wait


def test_enums_match_the_backend():
    """The backend refuses anything outside its enums; drift means the agent is told a value
    is valid that gets refused on every call."""
    models = (APP / "models.py").read_text()

    def literal(name: str) -> list[str]:
        body = models.split(f"{name} = Literal[", 1)[1].split("]", 1)[0]
        return re.findall(r'"(\w+)"', body)

    assert provision.ISSUES == literal("MissionIssue")
    assert provision.OUTCOMES == literal("Outcome")


def test_only_the_tenant_agent_allows_a_language_override():
    """Outbound calls to the tenant set their language; ElevenLabs refuses the override unless
    the agent allows it. The caller agent must stay in English for the landlord."""
    tenant = provision.agent_config("tenant", [])
    assert tenant["platform_settings"]["overrides"]["conversation_config_override"]["agent"]["language"]
    assert "platform_settings" not in provision.agent_config("caller", [])


def test_greetings_cover_the_same_languages():
    for key in ("intake", "tenant"):
        assert set(provision.AGENTS[key]["greetings"]) == set(provision.LANGUAGES), key


def test_caller_can_navigate_phone_menus_and_voicemail():
    built_in = provision.agent_config("caller", [])["conversation_config"]["agent"]["prompt"]["built_in_tools"]
    assert {"end_call", "play_keypad_touch_tone", "voicemail_detection"} <= set(built_in)
