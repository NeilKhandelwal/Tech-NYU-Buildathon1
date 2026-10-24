"""The live call log (app/live.py): lines land as `transcript` events the moment they are said,
get an English translation after, attach to the case once there is one, and the log falls
back to polling when ElevenLabs' monitoring socket isn't available. No network: ElevenLabs is
faked at the function boundary."""
import asyncio

import pytest

from app import hcdb, live, llm, monitor


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HC_DATABASE_URL", f"sqlite:///{tmp_path / 'hc.db'}")
    hcdb.reset_engine()
    hcdb.metadata.create_all(hcdb.engine())
    translated = []

    async def fake_translate(text, target):
        translated.append(text)
        return f"EN<{text}>"

    monkeypatch.setattr(llm, "translate", fake_translate)
    yield translated
    hcdb.reset_engine()


def lines(conversation_id="conv-1"):
    return hcdb.conversation_events(conversation_id, ("transcript",))


def test_a_line_is_logged_then_translated(db):
    asyncio.run(live.Call("conv-1", "intake").line("user", "No tengo calefacción"))
    [e] = lines()
    assert (e["actor"], e["text_original"], e["text_en"]) == ("resident", "No tengo calefacción",
                                                               "EN<No tengo calefacción>")
    assert e["case_id"] is None and e["payload"]["role"] == "user"


def test_agent_lines_are_attributed_to_the_agent_on_the_call(db):
    asyncio.run(live.Call("conv-1", "callback").line("agent", "Hola"))
    assert lines()[0]["actor"] == "callback_agent"


def test_lines_after_the_case_is_filed_carry_its_id_and_skip_translating_english(db):
    case = hcdb.insert_case(resident_phone="5550000000", language="en", address="1 Main St")
    hcdb.add_event(case["id"], "intake_call", "intake_agent", payload={"conversation_id": "conv-1"})
    asyncio.run(live.Call("conv-1", "intake").line("user", "It's freezing in here"))
    [e] = lines()
    assert e["case_id"] == case["id"] and e["text_en"] == "It's freezing in here"
    assert db == []  # no LLM call for an English-speaking resident


def test_a_correction_rewrites_the_cut_off_agent_line(db):
    call = live.Call("conv-1", "intake")
    asyncio.run(call.line("agent", "Your case number is H C four eight two nine one three"))
    fixed = asyncio.run(call.correct(monitor.Correction(
        "Your case number is H C four eight two nine one three", "Your case number is H C four")))
    assert fixed and lines()[0]["text_original"] == "Your case number is H C four"


def test_without_monitoring_the_transcript_is_polled_until_the_call_ends(db, monkeypatch):
    pages = iter([
        {"status": "in-progress", "transcript": [{"role": "agent", "message": "Hello"}]},
        {"status": "in-progress", "transcript": [{"role": "agent", "message": "Hello"},
                                                 {"role": "user", "message": "Hay un bache"}]},
        {"status": "done", "transcript": [{"role": "agent", "message": "Hello"},
                                          {"role": "user", "message": "Hay un bache"},
                                          {"role": "agent", "message": "Gracias"}]},
    ])

    async def fake_details(client, conversation_id):
        return next(pages)

    monkeypatch.setattr(live, "details", fake_details)
    monkeypatch.setattr(live, "POLL_S", 0)
    asyncio.run(live.relay_polling(live.Call("conv-1", "intake"), client=None))
    assert [e["text_original"] for e in lines()] == ["Hello", "Hay un bache", "Gracias"]


def test_a_relay_logs_start_and_end_even_when_nothing_was_said(db, monkeypatch):
    async def fake_details(client, conversation_id):
        return {"status": "done", "metadata": {"phone_call": {"external_number": "+15551234567"}},
                "transcript": []}

    async def no_socket(conversation_id, key):
        return None

    monkeypatch.setattr(live, "details", fake_details)
    monkeypatch.setattr(monitor, "open_socket", no_socket)
    asyncio.run(live.relay(live.Call("conv-1", "intake"), "key"))
    kinds = [e["type"] for e in hcdb.conversation_events("conv-1", ("call_started", "call_ended"))]
    assert kinds == ["call_started", "call_ended"]
    started = hcdb.conversation_events("conv-1", ("call_started",))[0]
    assert started["payload"]["caller"] == "5551234567"


def test_only_live_conversations_are_picked_up(db, monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"conversations": [
                {"conversation_id": "a", "agent_id": "ag-in", "status": "in-progress"},
                {"conversation_id": "b", "agent_id": "ag-in", "status": "done"},
                {"conversation_id": "c", "agent_id": "ag-in", "status": "initiated"}]}

    class FakeClient:
        async def get(self, url, params=None):
            assert params["agent_id"] == "ag-in"
            return FakeResponse()

    found = asyncio.run(live.live_conversations(FakeClient(), {"ag-in": "intake"}))
    assert found == [("a", "intake"), ("c", "intake")]


def test_agent_ids_read_either_intake_variable(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_INTAKE_AGENT_ID", raising=False)
    monkeypatch.setenv("ELEVENLABS_AGENT_ID", "old-name")
    monkeypatch.setenv("ELEVENLABS_CALLBACK_AGENT_ID", "cb")
    assert live.agent_ids() == {"old-name": "intake", "cb": "callback"}


def test_a_restart_mid_call_does_not_log_it_twice(db):
    hcdb.add_event(None, "call_started", "intake_agent", payload={"conversation_id": "conv-1"})
    assert live.already_logged("conv-1") and not live.already_logged("conv-2")


def test_the_poller_stays_off_without_a_key_or_when_disabled(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert live.start() is None
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("HC_LIVE_CALLS", "0")
    assert live.start() is None


def test_filing_the_case_attaches_the_call_logged_so_far(db):
    """create_case calls link_conversation: rows logged with no case join it, other calls don't."""
    hcdb.add_event(None, "call_started", "intake_agent", payload={"conversation_id": "conv-1"})
    hcdb.add_event(None, "transcript", "resident", text_original="Hola", payload={"conversation_id": "conv-1"})
    hcdb.add_event(None, "transcript", "resident", text_original="other", payload={"conversation_id": "conv-2"})
    case = hcdb.insert_case(resident_phone="5550000000", language="es", address="1 Main St")
    hcdb.link_conversation("conv-1", case["id"])
    assert sorted(e["type"] for e in hcdb.list_events(case["id"])) == ["call_started", "transcript"]
    assert hcdb.conversation_events("conv-2", ("transcript",))[0]["case_id"] is None
