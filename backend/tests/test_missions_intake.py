"""The intake agent's server tools, mission_update and mission_confirm (CONTRACT
"## Tenant advocate" / "### Server tools").

Each tool call is a separate stateless webhook, so these drive a whole intake the way the
agent would and check the mission the call builds up is one mission, linked to the call,
and only ready once the tenant has confirmed everything the outbound call needs.
"""
import pytest

from app import missions
from app.shared import BACKEND_DOWN

SECRET = "tool-secret"


@pytest.fixture
def intake(client, monkeypatch):
    monkeypatch.setenv("VOICE_TOOL_SECRET", SECRET)
    call_id = client.post("/calls", json={}).json()["id"]  # what POST /voice/session creates

    def run(tool: str, **body) -> str:
        r = client.post(f"/voice/tools/{tool}", json={"call_id": call_id, **body},
                        headers={"X-Voice-Secret": SECRET})
        assert r.status_code == 200, r.text  # failures are sentences, never a status code
        return r.json()["result"]

    run.call_id = call_id
    return run


def test_tools_refuse_without_the_shared_secret(client, monkeypatch):
    """On a public tunnel, anyone could otherwise point the agent at a landlord."""
    call_id = client.post("/calls", json={}).json()["id"]
    body = {"call_id": call_id, "tenant_name": "x"}
    monkeypatch.delenv("VOICE_TOOL_SECRET", raising=False)
    assert client.post("/voice/tools/mission_update", json=body).status_code == 503
    monkeypatch.setenv("VOICE_TOOL_SECRET", SECRET)
    for tool in ("mission_update", "mission_confirm"):
        assert client.post(f"/voice/tools/{tool}", json=body,
                           headers={"X-Voice-Secret": "wrong"}).status_code == 401
    assert client.get("/missions").json() == []


def test_first_update_creates_and_links_later_ones_patch_the_same_mission(intake, client):
    """Every turn is a fresh request; if the link lived anywhere but the call row, each turn
    would start a new half-filled mission."""
    assert intake("mission_update", tenant_name="Maria Lopez").startswith("Started mission M-101.")
    call = client.get(f"/calls/{intake.call_id}").json()
    assert (call["mission_id"], call["kind"]) == ("M-101", "intake")
    assert intake("mission_update", tenant_phone="555 123 4567", issue="no_heat").startswith(
        "Updated mission M-101.")
    assert len(client.get("/missions").json()) == 1
    m = client.get("/missions/M-101").json()
    assert (m["tenant_name"], m["tenant_phone"], m["issue"]) == ("Maria Lopez", "5551234567", "no_heat")
    assert client.get("/missions/M-101/dossier").json()["call_ids"] == [intake.call_id]


def test_update_says_what_is_still_needed(intake):
    """The agent learns what to ask next from the tool, not from guessing."""
    result = intake("mission_update", tenant_name="Maria Lopez", tenant_phone="5551234567")
    assert result.endswith("Still needed: landlord phone, issue, goal.")


def test_bad_values_come_back_as_correctable_sentences(intake, client):
    """A 422 leaves the agent stalling; a sentence lets it ask again. Nothing is written,
    so a bad number never sits on the mission waiting to be dialled."""
    assert intake("mission_update", issue="broken heater") == (
        "issue must be exactly one of: no_heat, no_hot_water, repair")
    assert intake("mission_update", landlord_phone="555 987 654").startswith(
        "The landlord phone has 9 digits")
    assert client.get("/missions").json() == []  # neither created a mission
    intake("mission_update", tenant_name="Maria Lopez", tenant_phone="5551234567")
    assert "9 digits" in intake("mission_update", tenant_phone="555123456")
    assert client.get("/missions/M-101").json()["tenant_phone"] == "5551234567"


def test_empty_parameters_are_left_alone(intake, client):
    """The LLM sends "" for a parameter it means to leave out; that must not blank a field."""
    intake("mission_update", tenant_name="Maria Lopez", goal="Heat back")
    assert intake("mission_update", goal="", issue="", tenant_phone="") == "Nothing to update."
    assert client.get("/missions/M-101").json()["goal"] == "Heat back"


def test_confirm_refuses_until_the_card_is_complete_and_names_the_gaps(intake, client):
    """The outbound call dials landlord_phone and argues from issue and goal; confirming
    without them sends the agent into a call it can't make or can't win."""
    assert intake("mission_confirm").startswith("No mission yet")
    intake("mission_update", tenant_name="Maria Lopez", tenant_phone="5551234567", issue="no_heat")
    assert intake("mission_confirm") == "Can't confirm yet. Missing: landlord phone, goal."
    assert client.get("/missions/M-101").json()["status"] == "draft"
    intake("mission_update", landlord_phone="5559876543", goal="heat restored by tomorrow")
    card = intake("mission_confirm")
    assert client.get("/missions/M-101").json()["status"] == "ready"
    assert card.startswith("Confirmed mission M-101: I'll call the landlord at 555 987 6543 for "
                           "Maria Lopez about no heat, asking for heat restored by tomorrow")
    assert card.count(". ") <= 1  # one sentence plus the limits line, not a paragraph


def test_card_reads_back_the_limits_the_agent_will_hold_to(intake):
    """The tenant has to hear what the agent may agree to before saying yes."""
    intake("mission_update", tenant_name="Maria Lopez", tenant_phone="5551234567",
           landlord_name="Elm St Management", landlord_phone="5559876543", issue="no_heat",
           goal="heat back", address="123 Elm St", unit="4B", deadline="2026-10-26",
           limits="Never agree to pay.")
    card = intake("mission_confirm")
    assert ("I'll call Elm St Management at 555 987 6543 for Maria Lopez at 123 Elm St, unit 4B "
            "about no heat, asking for heat back by 2026-10-26") in card
    assert card.endswith("Limits: Never agree to pay.")


def test_an_edit_after_confirm_needs_a_fresh_confirmation(intake, client):
    """The tenant agreed to one card; an outbound call on a changed one acts without consent."""
    intake("mission_update", tenant_name="Maria Lopez", tenant_phone="5551234567",
           landlord_phone="5559876543", issue="no_heat", goal="heat back")
    intake("mission_confirm")
    intake("mission_update", limits="Accept any date.")
    assert client.get("/missions/M-101").json()["status"] == "draft"
    intake("mission_confirm")
    assert client.get("/missions/M-101").json()["status"] == "ready"


def test_language_is_stored_as_the_primary_tag(intake, client):
    """The report-back call picks its language from this; 'es-MX' is the Spanish agent."""
    intake("mission_update", tenant_name="Maria", tenant_language="es-MX")
    assert client.get("/missions/M-101").json()["tenant_language"] == "es"


def test_an_unknown_call_reads_as_an_outage(intake, client):
    """A call id the backend has never seen gets the spoken outage line, not a 404."""
    r = client.post("/voice/tools/mission_update", json={"call_id": "CALL-999", "tenant_name": "x"},
                    headers={"X-Voice-Secret": SECRET})
    assert r.status_code == 200 and r.json()["result"] == BACKEND_DOWN
    assert client.get("/missions").json() == []


def test_intake_writes_broadcast_the_mission_and_the_linked_call(intake, monkeypatch):
    """The desk shows the call turning into a mission live; it only refetches on a frame."""
    frames = []

    async def record(type, id):
        frames.append((type, id))

    monkeypatch.setattr(missions, "broadcast", record)
    intake("mission_update", tenant_name="Maria Lopez")
    intake("mission_update", goal="heat")
    assert frames == [("mission", "M-101"), ("call", intake.call_id), ("mission", "M-101")]
