import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import provision  # noqa: E402

BACKEND_MAIN = Path(__file__).resolve().parents[2] / "backend" / "app" / "main.py"


def test_every_tool_takes_call_id_from_the_session_not_the_llm():
    """If the model could choose call_id, one caller's words could write to another's call."""
    for name in provision.TOOLS:
        body = provision.tool_config(name, "https://x", "s")["api_schema"]["request_body_schema"]
        assert body["properties"]["call_id"] == {"type": "string", "dynamic_variable": "call_id"}
        assert "call_id" in body["required"]


def test_every_tool_has_a_backend_route_and_sends_the_secret():
    """A renamed tool or route would 404 mid-call, and the agent would just say it failed."""
    routes = BACKEND_MAIN.read_text()
    for name in provision.TOOLS:
        schema = provision.tool_config(name, "https://x/", "s")["api_schema"]
        assert schema["url"] == f"https://x/voice/tools/{name}"
        assert f'@app.post("/voice/tools/{name}")' in routes
        assert schema["request_headers"] == {"X-Voice-Secret": "s"}


def test_issue_type_enum_matches_the_backend():
    """The backend refuses anything outside its enum; the two lists drifting means a
    classification the agent is told is valid gets refused on every call."""
    models = (BACKEND_MAIN.parent / "models.py").read_text()
    for t in provision.ISSUE_TYPES:
        assert f'"{t}"' in models.split("IssueType = Literal[", 1)[1].split("]", 1)[0]


def test_hangup_is_the_builtin_end_call():
    prompt = provision.agent_config(["t1"])["conversation_config"]["agent"]["prompt"]
    assert prompt["tool_ids"] == ["t1"]
    assert prompt["built_in_tools"]["end_call"]["params"] == {"system_tool_type": "end_call"}
