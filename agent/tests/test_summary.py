"""Transcript building for the post-call summary (the LLM call itself isn't unit-testable)."""
import sys
from pathlib import Path

from livekit.agents.llm import ChatMessage, FunctionCall

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from agent import history_text


def msg(role: str, text: str) -> ChatMessage:
    return ChatMessage(role=role, content=[text])


def test_history_text_labels_and_orders_turns():
    """The summary is only as good as the transcript it sees: both speakers, in order, tags stripped."""
    items = [
        msg("system", "you are the City services line"),
        msg("assistant", '<expr label="calm"/>City services, how can I help?'),
        msg("user", "my trash wasn't picked up"),
        FunctionCall(call_id="1", name="create_case", arguments="{}"),
        msg("assistant", "I've created case C-1001."),
    ]
    assert history_text(items) == (
        "Agent: City services, how can I help?\n"
        "Caller: my trash wasn't picked up\n"
        "Agent: I've created case C-1001."
    )


def test_history_text_needs_two_turns():
    """A hang-up after the greeting is not a call worth summarizing (and not worth an LLM call)."""
    assert history_text([]) == ""
    assert history_text([msg("assistant", "City services, how can I help?")]) == ""
    assert history_text([msg("assistant", "Hello?"), msg("user", "  ")]) == ""
