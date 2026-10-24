"""All 311 types, not only heat: what changes for a pothole, what stays the same for heat,
and a deploy that lands before schema.sql's new columns exist. SQLite stands in for Supabase."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, Table, Text, Uuid

from app import hc, hcdb, llm, webhooks


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("HC_DATABASE_URL", f"sqlite:///{tmp_path / 'hc.db'}")
    monkeypatch.delenv("HC_TOOL_SECRET", raising=False)
    hcdb.reset_engine()
    hcdb.metadata.create_all(hcdb.engine())

    async def no_geocode(address):
        return {}

    monkeypatch.setattr(hc, "geocode", no_geocode)
    sent = []

    async def fake_sms(case, kind, text_en):
        sent.append((kind, text_en))

    monkeypatch.setattr(hc, "send_sms", fake_sms)
    client = TestClient(hc.app)
    client.sent = sent
    yield client
    hcdb.reset_engine()


def file(api, **over) -> dict:
    body = {"resident_name": "Rosa Diaz", "resident_phone": "5551234567", "language": "es",
            "address": "Broadway and W 145th St", "conversation_id": "conv-9", **over}
    return api.post("/api/tools/create_case", json=body).json()


def test_a_pothole_is_filed_with_its_type_and_description(api):
    out = file(api, issue_type="pothole", description="Deep pothole in the crosswalk since Monday.")
    case = hcdb.get_case(out["case_id"])
    assert (case["issue_type"], case["description"]) == ("pothole", "Deep pothole in the crosswalk since Monday.")


def test_no_issue_type_is_heat_so_the_agent_live_today_keeps_working(api):
    """The heat-only agent sends no issue_type until it is re-provisioned."""
    out = file(api, days_without_heat=3, indoor_f=61)
    assert hcdb.get_case(out["case_id"])["issue_type"] == "heat_hot_water"


def test_an_unknown_type_is_filed_as_other_not_refused(api):
    out = file(api, issue_type="noise")
    assert hcdb.get_case(out["case_id"])["issue_type"] == "other"


def test_the_heat_rule_is_refused_for_other_problems(api):
    out = file(api, issue_type="streetlight", description="Out for a week.")
    r = api.post("/api/tools/check_violation", json={"case_id": out["case_id"], "indoor_f": 60}).json()
    assert "only applies" in r["error"]
    assert hcdb.get_case(out["case_id"])["is_violation"] is None


def test_confirmation_names_the_problem_not_heat(api):
    out = file(api, issue_type="missed_pickup", description="Trash not collected Tuesday.")
    api.post("/api/tools/submit_case", json={"case_id": out["case_id"], "resident_confirmed": True})
    kind, text = api.sent[0]
    assert kind == "confirmation" and "missed trash pickup" in text and "no-heat" not in text


def test_pre_written_spanish_heat_text_is_not_used_for_a_pothole():
    case = {"language": "es", "case_number": "HC-1", "issue_type": "pothole",
            "caseworker_action": None, "resident_response": None}
    assert hc.localized(case, "confirmation") is None
    assert "calefacción" in hc.localized({**case, "issue_type": "heat_hot_water"}, "confirmation")


def test_only_heat_gets_building_history(monkeypatch):
    called = []

    async def lookup(address, borough):
        called.append(address)
        return {"open_violations": 3}

    monkeypatch.setattr(webhooks.building, "building_lookup", lookup)
    import asyncio
    assert asyncio.run(webhooks.building_history({"issue_type": "pothole", "address": "x"})) is None
    assert asyncio.run(webhooks.building_history({"issue_type": None, "address": "y"})) == {"open_violations": 3}
    assert called == ["y"]


def test_suggestion_for_other_problems_does_not_need_a_temperature():
    assert llm.suggest({"issue_type": "pothole", "address": "x", "description": "deep"}) == "schedule_inspection"
    assert llm.suggest({"issue_type": "pothole", "address": "x"}) == "need_more_info"


def test_create_case_attaches_the_live_log_already_written(api):
    """The live log writes lines before the agent files the case (case_id null)."""
    hcdb.add_event(None, "call_started", "intake_agent", payload={"conversation_id": "conv-9"})
    hcdb.add_event(None, "transcript", "resident", text_original="Hola", payload={"conversation_id": "conv-9"})
    hcdb.add_event(None, "transcript", "resident", text_original="other call", payload={"conversation_id": "conv-x"})
    out = file(api, issue_type="water")
    linked = [e["type"] for e in hcdb.list_events(out["case_id"])]
    assert linked.count("transcript") == 1 and "call_started" in linked


def test_callback_tells_the_agent_what_the_report_was_about(api, monkeypatch):
    out = file(api, issue_type="animal", description="Raccoons in the courtyard.")
    api.post(f"/api/cases/{out['case_id']}/action",
             json={"caseworker_action": "schedule_inspection", "caseworker_message_en": "We'll visit."})
    dialed = {}

    async def fake_call(agent_id, to, variables, language=None):
        dialed.update(variables)
        return "conv-cb"

    monkeypatch.setattr(hc.elevenlabs, "outbound_call", fake_call)
    monkeypatch.setattr(hc.elevenlabs, "env", lambda name: "agent-cb")
    assert api.post(f"/api/cases/{out['case_id']}/callback").status_code == 200
    assert dialed["issue_label"] == "animal problem"


def test_a_deploy_before_the_sql_still_reads_and_files_cases(tmp_path, monkeypatch):
    """Fly can get this code before anyone runs schema.sql's `add column`; every query must
    keep working with issue_type/description read as None."""
    monkeypatch.setenv("HC_DATABASE_URL", f"sqlite:///{tmp_path / 'old.db'}")
    hcdb.reset_engine()
    old = MetaData()
    Table("cases", old, *[Column(c.name, c.type, primary_key=c.primary_key)
                          for c in hcdb.cases.columns if c.name not in ("issue_type", "description")])
    Table("events", old, Column("id", Uuid, primary_key=True), *[
        Column(c.name, c.type) for c in hcdb.events.columns if c.name != "id"])
    old.create_all(hcdb.engine())
    case = hcdb.insert_case(resident_phone="5550000000", language="en", address="1 Main St",
                            issue_type="pothole", description="deep")
    assert case["issue_type"] is None and case["description"] is None
    assert hcdb.list_cases()[0]["case_number"] == case["case_number"]
    assert hcdb.update_case(case["id"], issue_type="pothole")["status"] == "intake"
    hcdb.reset_engine()


def test_text_column_type_is_plain_text():
    # schema.sql adds both as `text`; a different type here would drift from Supabase
    assert isinstance(hcdb.cases.c.issue_type.type, Text) and isinstance(hcdb.cases.c.description.type, Text)
