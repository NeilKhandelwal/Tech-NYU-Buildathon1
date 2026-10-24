"""add_note is one POST: the note it was given is the note the case gets."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import agent as mod
from agent import BACKEND_DOWN, Assistant


class FakeResponse:
    status_code = 201

    def raise_for_status(self):
        pass


class RecordingClient:
    """Stands in for httpx.AsyncClient and records every request it is asked to make."""

    requests: list[tuple[str, str, dict | None]] = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def get(self, url, **kw):
        self.requests.append(("GET", url, None))
        return FakeResponse()

    async def patch(self, url, json=None, **kw):
        self.requests.append(("PATCH", url, json))
        return FakeResponse()

    async def post(self, url, json=None, **kw):
        self.requests.append(("POST", url, json))
        return FakeResponse()


@pytest.mark.asyncio
async def test_add_note_posts_the_note_and_reads_nothing_first(monkeypatch):
    """A GET-then-PATCH of the whole notes string loses a note whenever staff and the agent
    write in the same second, and records a rewrite instead of the note. One POST can't."""
    a = Assistant()  # before patching httpx: the LLM client is built here and needs the real one
    a.current_case = "C-1001"
    RecordingClient.requests = []
    monkeypatch.setattr(mod.httpx, "AsyncClient", RecordingClient)

    assert await a.add_note(None, "caller says the pothole is worse") == "Note added to case C-1001"
    assert RecordingClient.requests == [
        ("POST", f"{mod.BACKEND}/cases/C-1001/notes", {"text": "caller says the pothole is worse"})
    ]


@pytest.mark.asyncio
async def test_add_note_speaks_a_backend_failure_instead_of_raising(monkeypatch):
    """A tool must never raise into the session: the caller hears the failure or hears nothing."""

    class FailingClient(RecordingClient):
        async def post(self, url, json=None, **kw):
            raise RuntimeError("connection refused")

    a = Assistant()
    a.current_case = "C-1001"
    monkeypatch.setattr(mod.httpx, "AsyncClient", FailingClient)

    assert await a.add_note(None, "anything") == BACKEND_DOWN
