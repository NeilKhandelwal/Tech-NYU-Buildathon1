"""Missions, the dossier and escalations (CONTRACT "## Tenant advocate").

A mission is what the caller agent acts on when it phones a landlord for a tenant, so the
rules here are about never letting it act on a number it can't dial, a card the tenant
didn't confirm, or another city's tenant.
"""
from sqlalchemy import insert

from app import db, heat, missions
from app.models import HeatReading, HeatSummary

MARIA = {"tenant_name": "Maria Lopez", "tenant_phone": "(555) 123-4567"}
READY = {**MARIA, "landlord_name": "Elm St Management", "landlord_phone": "555-987-6543",
         "issue": "no_heat", "goal": "Heat restored by tomorrow", "address": "123 Elm St",
         "unit": "4B"}


def create(client, **body):
    r = client.post("/missions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_stores_digits_and_a_primary_language_tag(client):
    """The outbound lane dials tenant_phone/landlord_phone as stored and picks the agent's
    language from tenant_language, so both must already be in the form those calls take."""
    m = create(client, **MARIA, landlord_phone="555.987.6543", tenant_language="ES-mx")
    assert (m["id"], m["status"]) == ("M-101", "draft")
    assert m["tenant_phone"] == "5551234567" and m["landlord_phone"] == "5559876543"
    assert m["tenant_language"] == "es"
    assert (m["address"], m["issue"], m["outcome"]) == ("", None, None)
    assert create(client, **MARIA)["tenant_language"] == "en"  # the column default


def test_phones_that_are_not_ten_digits_are_refused(client):
    """A nine-digit number is an STT slip; storing it means the agent later dials nobody."""
    assert client.post("/missions", json={**MARIA, "tenant_phone": "555 123 456"}).status_code == 422
    assert client.post("/missions", json={**MARIA, "landlord_phone": "1 555 987 65432"}).status_code == 422
    m = create(client, **MARIA, landlord_phone="")  # "" is "not known yet", not a bad number
    assert client.patch(f"/missions/{m['id']}", json={"landlord_phone": "12345"}).status_code == 422
    assert client.get("/missions").json()[0]["landlord_phone"] == ""


def test_bad_issue_and_status_values_are_refused(client):
    """issue picks the legal script; status past ready belongs to the call flow, so staff
    setting 'committed' by hand would fake an outcome the landlord never gave."""
    assert client.post("/missions", json={**MARIA, "issue": "noise"}).status_code == 422
    m = create(client, **READY)
    for status in ("committed", "calling", "escalated", "draft"):
        assert client.patch(f"/missions/{m['id']}", json={"status": status}).status_code == 422
    assert client.get(f"/missions/{m['id']}").json()["status"] == "draft"


def test_ready_needs_what_the_outbound_call_needs(client):
    """ready is what lets POST /call start; a mission with no landlord phone can't be called."""
    m = create(client, **MARIA, issue="no_heat", goal="Heat back")
    r = client.patch(f"/missions/{m['id']}", json={"status": "ready"})
    assert r.status_code == 422 and "landlord phone" in r.json()["detail"]
    r = client.patch(f"/missions/{m['id']}", json={"status": "ready", "landlord_phone": "5559876543"})
    assert r.status_code == 200 and r.json()["status"] == "ready"


def test_ready_is_refused_once_a_call_is_live(client):
    """From calling, ready would let a second outbound call start against the same landlord."""
    m = create(client, **READY)
    with db.connect() as conn:
        missions.write_mission(conn, db.mission_pk(m["id"]), status="calling")
    assert client.patch(f"/missions/{m['id']}", json={"status": "ready"}).status_code == 409
    assert client.patch(f"/missions/{m['id']}", json={"status": "closed"}).json()["status"] == "closed"


def test_patch_changes_only_what_it_sends(client):
    """An edit to the goal must not blank the limits the tenant set: those bound the agent."""
    m = create(client, **READY, limits="Never agree to pay.")
    p = client.patch(f"/missions/{m['id']}", json={"goal": "Heat by Friday"}).json()
    assert p["goal"] == "Heat by Friday" and p["limits"] == "Never agree to pay."
    assert p["updated_at"] >= m["updated_at"]
    assert client.patch("/missions/M-999", json={"goal": "x"}).status_code == 404


def test_list_is_newest_first_and_unknown_ids_are_404(client):
    """The desk shows the newest tenant first; a typo'd id must 404, not 500."""
    create(client, **MARIA)
    create(client, **{**MARIA, "tenant_name": "Ana"})
    assert [m["id"] for m in client.get("/missions").json()] == ["M-102", "M-101"]
    for bad in ("M-999", "M-abc", "M-99999999999"):
        assert client.get(f"/missions/{bad}").status_code == 404


def test_missions_are_scoped_to_the_city(client, monkeypatch):
    """Another city's deployment must not see or edit this city's tenants."""
    m = create(client, **READY)
    monkeypatch.setenv("CITY_ID", "2")
    assert client.get("/missions").json() == []
    assert client.get(f"/missions/{m['id']}").status_code == 404
    assert client.get(f"/missions/{m['id']}/dossier").status_code == 404
    assert client.patch(f"/missions/{m['id']}", json={"goal": "x"}).status_code == 404
    assert client.post(f"/missions/{m['id']}/escalations", json={"kind": "hpd_complaint"}).status_code == 404
    monkeypatch.setenv("CITY_ID", "1")
    assert client.get(f"/missions/{m['id']}").json()["goal"] == READY["goal"]


def add_commitment(mission_pk: int, call_pk: int | None, what: str, due: str | None = None):
    with db.connect() as conn:
        conn.execute(insert(db.commitments).values(mission_id=mission_pk, call_id=call_pk,
                                                   party="landlord", what=what, due=due,
                                                   created_at="2026-10-21T15:00:00Z"))


def test_dossier_collects_everything_about_the_mission(client):
    """The caller agent and the escalation read the mission only through the dossier, so a
    commitment or call it misses is one the landlord gets to forget."""
    m = create(client, **READY)
    other = create(client, **MARIA)
    pk = db.mission_pk(m["id"])
    call = client.post("/calls", json={}).json()["id"]
    client.post("/calls", json={})  # a case call: not part of the mission
    with db.connect() as conn:
        conn.execute(db.calls.update().where(db.calls.c.id == db.call_pk(call))
                     .values(mission_id=pk, kind="outbound"))
        conn.execute(insert(db.tenant_questions).values(
            mission_id=pk, call_id=db.call_pk(call), question="Thursday instead?",
            created_at="2026-10-21T15:00:00Z"))
    add_commitment(pk, db.call_pk(call), "Fix the boiler", "2026-10-25T18:00")
    add_commitment(db.mission_pk(other["id"]), None, "Someone else's promise")

    d = client.get(f"/missions/{m['id']}/dossier").json()
    assert d["mission"] == client.get(f"/missions/{m['id']}").json()
    assert d["heat"] == {"readings": [], "violations": 0, "worst": None}
    assert [(c["what"], c["call_id"], c["mission_id"]) for c in d["commitments"]] == [
        ("Fix the boiler", call, m["id"])]
    assert [(q["question"], q["status"], q["call_id"]) for q in d["questions"]] == [
        ("Thursday instead?", "pending", call)]
    assert d["call_ids"] == [call]


# ---- escalations ----

def reading(taken_at, indoor, outdoor, required, violation, rid=1):
    return HeatReading(id=rid, mission_id="M-101", taken_at=taken_at, indoor_f=indoor,
                       outdoor_f=outdoor, source="voice", required_f=required, violation=violation)


def with_heat(monkeypatch, *readings):
    """heat.summary is the heat lane's; the template only has to use what it returns."""
    violations = [r for r in readings if r.violation]
    summary = HeatSummary(readings=list(readings), violations=len(violations),
                          worst=min(violations, key=lambda r: r.indoor_f, default=None))
    monkeypatch.setattr(heat, "summary", lambda conn, pk: summary)


def test_hpd_complaint_states_the_actual_violations(client, monkeypatch):
    """HPD acts on dated readings against the legal minimum; a draft that says only 'no heat'
    is a complaint an inspector can close as unverified."""
    with_heat(monkeypatch,
              reading("2026-10-20T23:00:00Z", 58.0, 41.0, 68, True),         # 7pm NYC, EDT
              reading("2026-10-21T07:30:00Z", 60.0, None, 62, True, rid=2),  # 3:30am
              reading("2026-10-21T17:00:00Z", 70.0, 50.0, 68, False, rid=3))
    m = create(client, **READY)
    add_commitment(db.mission_pk(m["id"]), None, "Send a technician", "2026-10-25T18:00")
    r = client.post(f"/missions/{m['id']}/escalations", json={"kind": "hpd_complaint"})
    assert r.status_code == 201
    e = r.json()
    assert (e["kind"], e["status"], e["mission_id"], e["approved_at"]) == (
        "hpd_complaint", "draft", m["id"], None)
    body = e["body"]
    for fact in ("Maria Lopez", "123 Elm St", "unit 4B", "Elm St Management", "5559876543",
                 "no heat", "3 indoor temperature reading(s) logged; 2 below",
                 "Tue Oct 20 2026, 7:00 PM: 58°F inside, 41°F outside; legal minimum 68°F — below",
                 "Wed Oct 21 2026, 3:30 AM: 60°F inside, outdoor temperature unknown; legal minimum 62°F",
                 "Send a technician", "not legal advice"):
        assert fact in body, fact
    # the violations come before the compliant reading: they are what the complaint rests on
    assert body.index("58°F") < body.index("60°F") < body.index("Other readings") < body.index("70°F")


def test_drafts_read_cleanly_with_no_heat_data(client):
    """A repair mission has no readings; the draft must still say so, not print None or []."""
    m = create(client, **{**MARIA, "issue": "repair"})
    for kind, title in (("hpd_complaint", "HPD complaint"), ("hp_action", "HP Action")):
        body = client.post(f"/missions/{m['id']}/escalations", json={"kind": kind}).json()["body"]
        assert body.startswith(title)
        assert "No indoor temperature readings have been logged." in body
        assert "No calls to the landlord are on record yet." in body
        assert "None" not in body and "[]" not in body
        assert "not legal advice" in body


def test_hp_action_organises_the_same_facts_for_court(client, monkeypatch):
    """An HP Action names the tenant as petitioner and the owner as respondent and asks for
    an order; the facts must be the same ones the HPD complaint used."""
    with_heat(monkeypatch, reading("2026-10-20T23:00:00Z", 58.0, 41.0, 68, True))
    m = create(client, **READY, deadline="2026-10-26")
    body = client.post(f"/missions/{m['id']}/escalations", json={"kind": "hp_action"}).json()["body"]
    for fact in ("Petitioner (tenant): Maria Lopez", "Respondent (owner / landlord): Elm St Management",
                 "Premises: 123 Elm St, apartment 4B", "58°F inside, 41°F outside; legal minimum 68°F",
                 "correct the conditions: Heat restored by tomorrow by 2026-10-26"):
        assert fact in body, fact


def test_approving_an_escalation_escalates_the_mission(client):
    """Approval is the tenant's sign-off; the mission must show it went past the landlord."""
    m = create(client, **READY)
    bad = client.post(f"/missions/{m['id']}/escalations", json={"kind": "lawsuit"})
    assert bad.status_code == 422
    e = client.post(f"/missions/{m['id']}/escalations", json={"kind": "hpd_complaint"}).json()
    assert client.get(f"/missions/{m['id']}").json()["status"] == "draft"  # a draft escalates nothing
    a = client.post(f"/missions/{m['id']}/escalations/{e['id']}/approve").json()
    assert a["status"] == "approved" and a["approved_at"]
    assert client.get(f"/missions/{m['id']}").json()["status"] == "escalated"
    again = client.post(f"/missions/{m['id']}/escalations/{e['id']}/approve").json()
    assert again["approved_at"] == a["approved_at"]  # a re-sent approve isn't a new sign-off
    assert [x["status"] for x in client.get(f"/missions/{m['id']}/escalations").json()] == ["approved"]


def test_an_escalation_is_only_reachable_through_its_own_mission(client):
    """Approving M-102's draft through M-101's URL would escalate the wrong tenant."""
    a, b = create(client, **READY), create(client, **READY)
    e = client.post(f"/missions/{b['id']}/escalations", json={"kind": "hp_action"}).json()
    assert client.post(f"/missions/{a['id']}/escalations/{e['id']}/approve").status_code == 404
    assert client.post(f"/missions/{a['id']}/escalations/99999999999/approve").status_code == 404
    assert client.get(f"/missions/{a['id']}").json()["status"] == "draft"
    assert client.get(f"/missions/{a['id']}/escalations").json() == []


def test_every_mission_write_broadcasts(client, monkeypatch):
    """The dashboard only refetches on a frame; a silent write is a stale screen."""
    frames = []

    async def record(type, id):
        frames.append((type, id))

    monkeypatch.setattr(missions, "broadcast", record)
    m = create(client, **READY)
    client.patch(f"/missions/{m['id']}", json={"goal": "x"})
    e = client.post(f"/missions/{m['id']}/escalations", json={"kind": "hp_action"}).json()
    client.post(f"/missions/{m['id']}/escalations/{e['id']}/approve")
    assert frames == [("mission", "M-101")] * 4
