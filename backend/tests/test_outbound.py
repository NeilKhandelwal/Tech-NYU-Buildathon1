"""Outbound calls and the caller agent's tools (CONTRACT "## Tenant advocate").

The agent is negotiating for someone who isn't on the line. These tests hold the rules that
keep it honest about that: it can't dial a mission the tenant hasn't confirmed, it waits for
the tenant before agreeing to anything, and every call it starts is one that really rang.
"""
import asyncio
import time

import httpx
import pytest
from sqlalchemy import insert, select

from app import db, elevenlabs, monitor, outbound
from tests.test_voice import HOOK_SECRET, SECRET, post_call, signed

HEADERS = {"X-Voice-Secret": SECRET}


def add_mission(status="ready", tenant_language="en", **fields) -> str:
    ts = "2026-10-24T12:00:00Z"
    values = dict(city_id=db.current_city_id(), tenant_name="Maria Lopez",
                  tenant_phone="5551234567", tenant_language=tenant_language,
                  address="123 Elm St", unit="4B", landlord_name="Elm St Management",
                  landlord_phone="5559876543", issue="no_heat", goal="Heat restored by tomorrow",
                  limits="May accept a repair visit within 48 hours. Never agree to pay.",
                  status=status, created_at=ts, updated_at=ts)
    with db.connect() as conn:
        pk = conn.execute(insert(db.missions).values(**{**values, **fields})).inserted_primary_key[0]
    return db.mission_id(pk)


def mission_row(mission_id: str):
    with db.connect() as conn:
        return conn.execute(select(db.missions).where(
            db.missions.c.id == db.mission_pk(mission_id),
            db.missions.c.city_id == db.current_city_id())).fetchone()


def call_count() -> int:
    with db.connect() as conn:
        return len(conn.execute(select(db.calls.c.id).where(
            db.calls.c.city_id == db.current_city_id())).fetchall())


@pytest.fixture
def dialled(client, monkeypatch):
    """ElevenLabs configured and the dial faked; records every dial and relay start."""
    for name in ("ELEVENLABS_API_KEY", "ELEVENLABS_PHONE_NUMBER_ID", "ELEVENLABS_CALLER_AGENT_ID",
                 "ELEVENLABS_TENANT_AGENT_ID"):
        monkeypatch.setenv(name, name.lower())
    monkeypatch.setenv("VOICE_TOOL_SECRET", SECRET)
    log = {"dials": [], "relays": []}

    async def fake_call(agent_id, to_number, dynamic_variables, language=None):
        log["dials"].append((agent_id, to_number, dynamic_variables, language))
        return f"conv_{len(log['dials'])}"

    monkeypatch.setattr(elevenlabs, "outbound_call", fake_call)
    monkeypatch.setattr(monitor, "start", lambda *a: log["relays"].append(a))
    return log


def start_call(client, mission_id: str) -> str:
    r = client.post(f"/missions/{mission_id}/call")
    assert r.status_code == 201, r.text
    return r.json()["id"]


def tool(client, name: str, call_id: str, **body) -> str:
    r = client.post(f"/voice/tools/{name}", json={"call_id": call_id, **body}, headers=HEADERS)
    assert r.status_code == 200, r.text  # failures are sentences, never a status code
    return r.json()["result"]


# ---- dialling ----

def test_a_draft_mission_is_not_dialled(client, dialled):
    """The tenant hasn't confirmed the card; the agent would be negotiating without limits."""
    mid = add_mission(status="draft")
    assert client.post(f"/missions/{mid}/call").status_code == 409
    calling = add_mission(status="calling")  # and never a second call on top of a live one
    assert client.post(f"/missions/{calling}/call").status_code == 409
    assert dialled["dials"] == [] and call_count() == 0


def test_unconfigured_elevenlabs_is_503_and_writes_nothing(client, monkeypatch):
    mid = add_mission()
    monkeypatch.delenv("ELEVENLABS_CALLER_AGENT_ID", raising=False)
    assert client.post(f"/missions/{mid}/call").status_code == 503
    assert call_count() == 0 and mission_row(mid).status == "ready"


def test_a_failed_dial_leaves_no_call_row(client, dialled, monkeypatch):
    """The row must exist to carry {{call_id}} into the dial; if the dial fails it must go,
    or the desk shows a live call that never rang and the mission looks in progress."""
    mid = add_mission()

    async def refused(*a, **k):
        raise httpx.HTTPStatusError("422", request=httpx.Request("POST", "x"),
                                    response=httpx.Response(422))

    monkeypatch.setattr(elevenlabs, "outbound_call", refused)
    assert client.post(f"/missions/{mid}/call").status_code == 502
    assert call_count() == 0 and mission_row(mid).status == "ready"


def test_calling_the_landlord(client, dialled):
    mid = add_mission()
    r = client.post(f"/missions/{mid}/call")
    assert r.status_code == 201
    call = r.json()
    assert call["kind"] == "outbound" and call["mission_id"] == mid
    assert call["conversation_id"] == "conv_1"
    agent_id, to, variables, _ = dialled["dials"][0]
    # the tools find their mission through call_id; it must be the row we just returned
    assert (agent_id, to) == ("elevenlabs_caller_agent_id", "5559876543")
    assert variables["call_id"] == call["id"] and variables["mission_id"] == mid
    assert variables["tenant_name"] == "Maria Lopez"
    assert mission_row(mid).status == "calling"
    assert dialled["relays"] == [(1, "conv_1", "en")]


def test_a_finished_attempt_can_be_retried(client, dialled):
    mid = add_mission(status="no_answer", outcome="Nobody picked up")
    start_call(client, mid)
    row = mission_row(mid)
    assert row.status == "calling" and row.outcome is None  # the old outcome is not this call's


def test_report_call_goes_to_the_tenant_in_their_language(client, dialled):
    mid = add_mission(status="committed", tenant_language="es", outcome="Boiler fix tomorrow")
    call_id = start_call(client, mid)  # a commitment needs a call to belong to
    tool(client, "record_commitment", call_id, what="fix the boiler", due="2026-10-25T18:00")
    r = client.post(f"/missions/{mid}/report-call")
    assert r.status_code == 201 and r.json()["kind"] == "report"
    agent_id, to, variables, language = dialled["dials"][-1]
    assert (agent_id, to, language) == ("elevenlabs_tenant_agent_id", "5551234567", "es")
    assert "fix the boiler by 2026-10-25T18:00" in variables["commitments"]
    assert variables["call_id"] == r.json()["id"]


# ---- caller tools ----

def test_dossier_gives_the_agent_the_limits_and_heat(client, dialled):
    mid = add_mission()
    call_id = start_call(client, mid)
    text = tool(client, "get_dossier", call_id)
    for fact in ("Maria Lopez", "123 Elm St", "unit 4B", "no heat", "Heat restored by tomorrow",
                 "Never agree to pay", "No heat readings have been logged yet."):
        assert fact in text


def test_commitment_and_outcome(client, dialled):
    mid = add_mission()
    call_id = start_call(client, mid)
    # committed with nothing recorded would leave the report-back call with nothing to say
    assert "record_commitment first" in tool(client, "set_outcome", call_id, outcome="committed")
    assert mission_row(mid).status == "calling"
    assert tool(client, "record_commitment", call_id, what="send a technician", due="") \
        == "Recorded: the landlord will send a technician."
    c = client.get(f"/missions/{mid}/commitments").json()
    assert [(x["party"], x["what"], x["due"], x["call_id"]) for x in c] == \
        [("landlord", "send a technician", None, call_id)]
    tool(client, "set_outcome", call_id, outcome="committed", note="Technician tomorrow 9am")
    row = mission_row(mid)
    assert (row.status, row.outcome) == ("committed", "Technician tomorrow 9am")


def test_bad_outcome_is_a_correctable_sentence(client, dialled):
    mid = add_mission()
    call_id = start_call(client, mid)
    assert tool(client, "set_outcome", call_id, outcome="agreed") == \
        "outcome must be exactly one of: committed, refused, no_answer"
    assert mission_row(mid).status == "calling"


def test_tools_on_a_call_with_no_mission_are_an_outage_sentence(client, dialled):
    call_id = client.post("/calls", json={}).json()["id"]  # a case call, not a mission call
    assert "couldn't reach" in tool(client, "get_dossier", call_id)


# ---- ask_tenant ----

async def ask_and_answer(answer: str | None, answer_after_s: float = 0.3):
    """Run ask_tenant and, concurrently, the tenant's answer, against one app on one loop."""
    from app.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        async def tenant():
            await asyncio.sleep(answer_after_s)
            q = (await ac.get("/missions/M-101/questions")).json()
            seen = (q[-1]["status"], mission_row("M-101").status)
            if answer is None:
                return seen, None
            r = await ac.post(f"/missions/M-101/questions/{q[-1]['id']}/answer",
                              json={"answer": answer})
            return seen, r

        ask = ac.post("/voice/tools/ask_tenant", headers=HEADERS,
                      json={"call_id": "CALL-1", "question": "Thursday instead of tomorrow. Accept?"})
        return await asyncio.gather(ask, tenant())


def test_ask_tenant_returns_the_answer_that_arrives_mid_wait(client, dialled, monkeypatch):
    """The whole point: the agent holds the landlord while the tenant decides."""
    monkeypatch.setenv("ASK_TENANT_TIMEOUT_S", "10")
    start_call(client, add_mission())
    started = time.monotonic()
    tool_r, ((status, mission_status), answer_r) = asyncio.run(ask_and_answer("No, tomorrow or we file"))
    # the landlord is on hold: the answer goes back as soon as it lands, not at the timeout
    assert time.monotonic() - started < 5
    assert (status, mission_status) == ("pending", "waiting_on_tenant")  # seen while it waited
    assert answer_r.status_code == 200 and answer_r.json()["status"] == "answered"
    assert tool_r.json() == {"result": "The tenant says: No, tomorrow or we file"}
    assert mission_row("M-101").status == "calling"


def test_ask_tenant_expires_and_says_so(client, dialled, monkeypatch):
    monkeypatch.setenv("ASK_TENANT_TIMEOUT_S", "0.6")
    start_call(client, add_mission())
    tool_r, _ = asyncio.run(ask_and_answer(None, answer_after_s=0.1))
    assert tool_r.json()["result"] == outbound.NO_ANSWER_YET
    q = client.get("/missions/M-101/questions").json()
    assert [x["status"] for x in q] == ["expired"]
    assert mission_row("M-101").status == "calling"
    # a late answer must not look accepted: the agent already told the landlord it'd follow up
    late = client.post(f"/missions/M-101/questions/{q[0]['id']}/answer", json={"answer": "yes"})
    assert late.status_code == 409


def test_ask_tenant_translates_both_ways(client, dialled, monkeypatch):
    """A Spanish-speaking tenant reads the question in Spanish; the agent hears English."""
    monkeypatch.setenv("ASK_TENANT_TIMEOUT_S", "10")
    calls = []

    async def fake_translate(text, target):
        calls.append(target)
        return {"es": "¿Jueves?", "en": "No, tomorrow"}[target]

    monkeypatch.setattr(outbound.translate, "translate", fake_translate)
    start_call(client, add_mission(tenant_language="es"))
    tool_r, (_, answer_r) = asyncio.run(ask_and_answer("No, mañana"))
    assert answer_r.json()["answer"] == "No, mañana"  # stored as given
    assert answer_r.json()["question_translated"] == "¿Jueves?"
    assert tool_r.json()["result"] == "The tenant says: No, tomorrow"
    assert calls == ["es", "en"]


def test_answering_twice_is_409(client, dialled, monkeypatch):
    monkeypatch.setenv("ASK_TENANT_TIMEOUT_S", "10")
    start_call(client, add_mission())
    asyncio.run(ask_and_answer("yes"))
    qid = client.get("/missions/M-101/questions").json()[0]["id"]
    r = client.post(f"/missions/M-101/questions/{qid}/answer", json={"answer": "actually no"})
    assert r.status_code == 409
    assert client.get("/missions/M-101/questions").json()[0]["answer"] == "yes"


# ---- the call ends ----

def test_an_outbound_call_that_ends_without_an_outcome_is_no_answer(client, dialled, monkeypatch):
    """Otherwise the mission shows 'calling' forever and can never be redialled (409)."""
    monkeypatch.setenv("ELEVENLABS_WEBHOOK_SECRET", HOOK_SECRET)
    mid = add_mission()
    call_id = start_call(client, mid)
    raw, headers = signed(post_call(call_id))
    assert client.post("/voice/webhooks/post-call", content=raw, headers=headers).status_code == 200
    assert mission_row(mid).status == "no_answer"
    assert client.post(f"/missions/{mid}/call").status_code == 201


def test_call_end_keeps_an_outcome_the_agent_set(client, dialled):
    mid = add_mission()
    call_id = start_call(client, mid)
    tool(client, "set_outcome", call_id, outcome="refused", note="Landlord refused")
    assert asyncio.run(outbound.on_call_ended(db.call_pk(call_id))) is False
    assert mission_row(mid).status == "refused"
