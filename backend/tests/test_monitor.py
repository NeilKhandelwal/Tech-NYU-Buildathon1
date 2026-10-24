"""The live relay (app/monitor.py): monitor events in, subtitle lines out.

The tenant watches the landlord call as it happens. A line shown twice, or a tentative
half-sentence shown as final, is the tenant reading something nobody said.
"""
import asyncio

from sqlalchemy import select

from app import db, monitor
from tests.test_outbound import add_mission, start_call, dialled  # noqa: F401  (fixture)


def user(text):
    return {"type": "user_transcript", "user_transcription_event": {"user_transcript": text}}


def agent(text):
    return {"type": "agent_response", "agent_response_event": {"agent_response": text}}


def correction(original, corrected):
    return {"type": "agent_response_correction", "agent_response_correction_event": {
        "original_agent_response": original, "corrected_agent_response": corrected}}


def test_parser_keeps_only_final_lines():
    assert monitor.parse_event(user(" We can come Thursday. ")) == monitor.Line("user", "We can come Thursday.")
    assert monitor.parse_event(agent("Hello, I'm calling for Maria.")) == \
        monitor.Line("agent", "Hello, I'm calling for Maria.")
    assert monitor.parse_event(correction("Let me tell you all", "Let me")) == \
        monitor.Correction("Let me tell you all", "Let me")
    for noise in ({"type": "connected"}, {"type": "history_complete"},
                  {"type": "ping", "ping_event": {"event_id": 1}},
                  {"type": "audio", "audio_event": {"audio_base_64": "x"}},
                  {"type": "tentative_user_transcript",
                   "tentative_user_transcription_event": {"user_transcript": "We can"}},
                  {"type": "agent_chat_response_part", "text_response_part": {"type": "delta", "text": "Hel"}},
                  {"type": "vad_score"}, user("   "), agent(None), {"no": "type"}, ["not", "a", "dict"]):
        assert monitor.parse_event(noise) is None, noise


def lines(call_pk):
    with db.connect() as conn:
        return [(r.role, r.text, r.translated) for r in conn.execute(
            select(db.transcript).where(db.transcript.c.call_id == call_pk).order_by(db.transcript.c.id))]


def feed(call_pk, events, lang="es"):
    async def run():
        early = {}
        for e in events:
            await monitor.handle(call_pk, e, lang, early)
    asyncio.run(run())


def test_lines_are_stored_with_subtitles_and_a_correction_rewrites_in_place(client, dialled, monkeypatch):
    async def fake_translate(text, target):
        return f"[{target}] {text}"

    monkeypatch.setattr(monitor.translate, "translate", fake_translate)
    call_id = start_call(client, add_mission(tenant_language="es"))
    pk = db.call_pk(call_id)
    feed(pk, [agent("Hello, I'm calling about the heat at 123 Elm St, unit 4B"),
              {"type": "tentative_user_transcript"},
              user("Yeah, we know."),
              correction("Hello, I'm calling about the heat at 123 Elm St, unit 4B",
                         "Hello, I'm calling about the heat")])
    assert lines(pk) == [("agent", "Hello, I'm calling about the heat", "[es] Hello, I'm calling about the heat"),
                         ("user", "Yeah, we know.", "[es] Yeah, we know.")]
    # and the desk refetches the call: the line is visible through the API
    assert [t["text"] for t in client.get(f"/calls/{call_id}").json()["transcript"]] == \
        ["Hello, I'm calling about the heat", "Yeah, we know."]


def test_a_correction_before_its_line_still_makes_one_line(client, dialled):
    """The docs warn monitor events can arrive out of order."""
    pk = db.call_pk(start_call(client, add_mission()))
    feed(pk, [correction("We will escalate to HPD today", "We will"), agent("We will escalate to HPD today")],
         lang="en")
    assert lines(pk) == [("agent", "We will", None)]  # English tenant: no subtitle, no request


def test_relay_without_a_key_returns_without_connecting(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    async def boom(*a, **k):
        raise AssertionError("must not connect")

    monkeypatch.setattr(monitor, "open_socket", boom)
    asyncio.run(monitor.relay(1, "conv_1", "es"))


def test_relay_never_raises_into_the_app(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")

    async def broken(*a, **k):
        raise RuntimeError("socket exploded")

    monkeypatch.setattr(monitor, "open_socket", broken)
    asyncio.run(monitor.relay(1, "conv_1", "es"))  # logs and stops


def test_relay_reads_until_the_socket_closes(client, dialled, monkeypatch):
    """End to end over a fake socket: JSON frames in, lines out, clean stop at close."""
    import json
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    pk = db.call_pk(start_call(client, add_mission()))

    class FakeSocket:
        def __init__(self, frames):
            self.frames = frames

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.frames:
                raise StopAsyncIteration
            return self.frames.pop(0)

    frames = [json.dumps({"type": "connected"}), json.dumps(agent("Hi")), "not json",
              json.dumps({"type": "history_complete"}), json.dumps(user("Who is this?"))]

    async def fake_open(*a):
        return FakeSocket(frames)

    monkeypatch.setattr(monitor, "open_socket", fake_open)
    asyncio.run(monitor.relay(pk, "conv_1", "en"))
    assert lines(pk) == [("agent", "Hi", None), ("user", "Who is this?", None)]
