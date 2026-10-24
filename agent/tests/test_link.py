"""create_case links the live call to its case; if that link fails, it is retried later."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import agent as mod
from agent import Assistant


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"id": "C-1001", "lookup_code": "blue-river-maple", "notes": ""}


class FakeClient:
    """Stands in for httpx.AsyncClient: every request succeeds."""

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def post(self, *a, **kw):
        return FakeResponse()

    async def patch(self, *a, **kw):
        return FakeResponse()


@pytest.mark.asyncio
async def test_failed_call_link_is_retried_on_next_tool_call(monkeypatch):
    """A transient error on the link PATCH must not leave the case detached from its call for
    the rest of the conversation; the dashboard and audit trail depend on that link."""
    outcomes = [False, True]  # first link attempt fails, second succeeds
    linked_with = []

    async def fake_link(call_id, case_id, how):
        linked_with.append((call_id, case_id, how))
        return outcomes.pop(0)

    a = Assistant()  # before patching httpx: the LLM client is built here and needs the real one
    a.call_id = "CALL-7"
    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(mod, "link_call_case", fake_link)

    assert await a.create_case(None, "Ana Ruiz", "555 010 2020") == "Started case C-1001"
    assert a.current_case == "C-1001" and a.call_linked is False

    assert await a.update_case(None, issue_type="pothole") == "Updated case C-1001"
    assert a.call_linked is True
    assert linked_with == [("CALL-7", "C-1001", "created")] * 2

    # once linked, further tool calls don't POST again
    await a.update_case(None, description="on Main St")
    assert len(linked_with) == 2


@pytest.mark.asyncio
async def test_lookup_code_is_withheld_until_the_description_is_saved(monkeypatch):
    """Reading the code out early would hand it to a caller whose case is still half-filed, and
    the whole point of the code is that it is spoken exactly once, at the end of intake."""
    a = Assistant()
    a.call_id = "CALL-7"
    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)

    assert "blue" not in await a.create_case(None, "Ana Ruiz", "555 010 2020")
    assert a.lookup_code == "blue-river-maple"
    assert "blue" not in await a.update_case(None, issue_type="pothole")
    assert await a.update_case(None, description="on Main St") == (
        "Updated case C-1001. Lookup code blue-river-maple"
    )
