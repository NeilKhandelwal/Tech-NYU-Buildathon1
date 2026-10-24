"""Heat readings, the outdoor lookup, the summary the caller agent quotes, the tenant agent's
log_heat_reading tool, and the heat-check call (CONTRACT "### The heat rule", "### Endpoints").

The outdoor lookup is mocked everywhere: by default every Open-Meteo request fails, so a
test that forgets to mock it gets a null outdoor instead of reaching the network.
"""
import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import func, insert, select

from app import db, elevenlabs, heat
from app.models import HeatReading, HeatSummary

SECRET = "tool-secret"


@pytest.fixture(autouse=True)
def weather(monkeypatch):
    """Swap Open-Meteo for a handler the test controls; record every request it sees."""
    state = {"requests": [], "handler": lambda req: httpx.Response(503)}

    def route(request):
        state["requests"].append(request)
        return state["handler"](request)

    monkeypatch.setattr(heat, "_client",
                        lambda: httpx.AsyncClient(transport=httpx.MockTransport(route)))
    heat._outdoor_cache.clear()
    yield state
    heat._outdoor_cache.clear()


def open_meteo(temps: dict[str, float | None]):
    """A handler answering like Open-Meteo: hourly times in GMT, values in °F."""
    def handler(request):
        return httpx.Response(200, json={"hourly": {"time": list(temps), "temperature_2m": list(temps.values())}})
    return handler


def make_mission(phone="5551234567", language="es") -> str:
    """Missions are another lane's endpoints; insert the row the way that lane would."""
    ts = "2026-10-20T12:00:00Z"
    with db.connect() as conn:
        pk = conn.execute(insert(db.missions).values(
            city_id=db.current_city_id(), tenant_name="Maria Lopez", tenant_phone=phone,
            tenant_language=language, issue="no_heat", created_at=ts, updated_at=ts,
        )).inserted_primary_key[0]
    return db.mission_id(pk)


def reading(id, taken_at, indoor, outdoor) -> HeatReading:
    at = datetime.fromisoformat(taken_at)
    return HeatReading(id=id, mission_id="M-101", taken_at=taken_at, indoor_f=indoor,
                       outdoor_f=outdoor, source="staff",
                       required_f=heat.required_f(at, outdoor),
                       violation=heat.is_violation(at, indoor, outdoor))


def count_calls() -> int:
    with db.connect() as conn:
        return conn.execute(select(func.count()).select_from(db.calls)
                            .where(db.calls.c.city_id == db.current_city_id())).scalar()


# ---- outdoor temperature ----

def test_outdoor_is_the_utc_hour_of_the_reading_in_fahrenheit(weather):
    """Open-Meteo answers in GMT; the reading's hour must be picked in UTC or it's off by 4-5h."""
    weather["handler"] = open_meteo({"2026-10-20T22:00": 50.0, "2026-10-20T23:00": 41.0})
    at = datetime(2026, 10, 20, 23, 40, tzinfo=timezone.utc)  # 7:40pm EDT
    assert asyncio.run(heat.outdoor_temp_f(at)) == 41.0
    params = weather["requests"][0].url.params
    assert params["temperature_unit"] == "fahrenheit" and params["timezone"] == "GMT"
    assert params["hourly"] == "temperature_2m"
    assert (params["latitude"], params["longitude"]) == ("40.7128", "-74.006")
    assert params["start_date"] == params["end_date"] == "2026-10-20"


def test_recent_hours_use_the_forecast_api_and_old_ones_the_archive(weather):
    """The forecast API refuses hours older than ~92 days; the archive lags ~5 days."""
    recent = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    weather["handler"] = open_meteo({recent.strftime("%Y-%m-%dT%H:00"): 41.0})
    assert asyncio.run(heat.outdoor_temp_f(recent)) == 41.0
    assert weather["requests"][-1].url.host == "api.open-meteo.com"
    weather["handler"] = open_meteo({"2025-01-10T12:00": 30.0})
    assert asyncio.run(heat.outdoor_temp_f(datetime(2025, 1, 10, 12, 5, tzinfo=timezone.utc))) == 30.0
    assert weather["requests"][-1].url.host == "archive-api.open-meteo.com"


def test_outdoor_is_cached_per_hour(weather):
    """Ten readings in one evening hour must not be ten weather requests."""
    weather["handler"] = open_meteo({"2026-10-20T23:00": 41.0})
    for minute in (0, 15, 59):
        assert asyncio.run(heat.outdoor_temp_f(datetime(2026, 10, 20, 23, minute, tzinfo=timezone.utc))) == 41.0
    assert len(weather["requests"]) == 1


@pytest.mark.parametrize("handler", [
    lambda req: httpx.Response(500),
    lambda req: httpx.Response(200, json={"error": True}),
    lambda req: httpx.Response(200, json={"hourly": {"time": ["2026-10-20T01:00"], "temperature_2m": [40]}}),
    lambda req: httpx.Response(200, json={"hourly": {"time": ["2026-10-20T23:00"], "temperature_2m": [None]}}),
    lambda req: (_ for _ in ()).throw(httpx.ConnectTimeout("slow")),
])
def test_any_weather_failure_is_none_never_an_error(weather, handler):
    """A reading must still be logged when the weather is down; the rule handles None."""
    weather["handler"] = handler
    assert asyncio.run(heat.outdoor_temp_f(datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc))) is None


def test_a_failed_lookup_is_not_cached(weather):
    """An outage must not pin the hour to None; the next reading retries."""
    at = datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc)
    assert asyncio.run(heat.outdoor_temp_f(at)) is None
    weather["handler"] = open_meteo({"2026-10-20T23:00": 41.0})
    assert asyncio.run(heat.outdoor_temp_f(at)) == 41.0


# ---- summary and describe ----

def test_summary_counts_violations_and_picks_the_largest_shortfall(client):
    """The landlord hears the most damning reading: 60°F by day (8 under 68) beats 58°F at
    night (4 under 62), even though 58 is colder."""
    mid = make_mission()
    for body in ({"indoor_f": 58.0, "outdoor_f": 45.0, "taken_at": "2026-10-21T03:30:00Z"},  # night, -4
                 {"indoor_f": 60.0, "outdoor_f": 41.0, "taken_at": "2026-10-20T23:00:00Z"},  # day, -8
                 {"indoor_f": 70.0, "outdoor_f": 41.0, "taken_at": "2026-10-20T15:00:00Z"},  # fine
                 {"indoor_f": 60.0, "outdoor_f": 60.0, "taken_at": "2026-10-20T16:00:00Z"}):  # warm out
        assert client.post(f"/missions/{mid}/heat-readings", json=body).status_code == 201
    s = client.get(f"/missions/{mid}/heat-readings").json()
    assert s["violations"] == 2
    assert s["worst"]["indoor_f"] == 60.0 and s["worst"]["required_f"] == 68
    # oldest first: a timeline the dashboard and the dossier can read top to bottom
    assert [r["taken_at"] for r in s["readings"]] == sorted(r["taken_at"] for r in s["readings"])
    with db.connect() as conn:
        assert heat.summary(conn, db.mission_pk(mid)).model_dump() == s


def test_describe_quotes_the_worst_violation_in_local_time():
    """The caller agent reads this to a landlord: New York time, both temperatures, the minimum."""
    worst = reading(2, "2026-10-20T23:00:00Z", 58.0, 41.0)  # 7pm EDT, Tuesday
    text = heat.describe(HeatSummary(readings=[worst], violations=1, worst=worst))
    assert text == ("Logged 58°F inside at 7pm on Tuesday, October 20 with 41°F outside; "
                    "the legal minimum then was 68°F.")


def test_describe_mentions_how_many_violations_there_were():
    worst = reading(2, "2026-10-20T23:30:00Z", 58.5, 41.0)
    other = reading(1, "2026-10-21T03:30:00Z", 60.0, None)  # night: no outdoor needed
    text = heat.describe(HeatSummary(readings=[worst, other], violations=2, worst=worst))
    assert "58.5°F inside at 7:30pm on Tuesday, October 20" in text
    assert text.endswith("That is the worst of 2 readings below the legal minimum.")


def test_describe_a_night_violation_with_no_outdoor_omits_the_outside_temperature():
    worst = reading(1, "2026-10-21T03:30:00Z", 58.0, None)  # 11:30pm EDT
    assert heat.describe(HeatSummary(readings=[worst], violations=1, worst=worst)) == (
        "Logged 58°F inside at 11:30pm on Tuesday, October 20; the legal minimum then was 62°F.")


def test_describe_without_readings_or_violations_claims_nothing():
    """With no violation the agent must not imply one."""
    assert heat.describe(HeatSummary()) == "No heat readings have been logged yet."
    ok = reading(1, "2026-10-20T15:00:00Z", 70.0, 41.0)
    unknown = reading(2, "2026-10-20T16:00:00Z", 60.0, None)  # by day, outdoor unknown
    assert heat.describe(HeatSummary(readings=[ok], violations=0)) == (
        "1 heat reading logged; none was below the legal minimum.")
    assert heat.describe(HeatSummary(readings=[ok, unknown], violations=0)) == (
        "2 heat readings logged; none was below the legal minimum. 1 couldn't be checked "
        "because the outdoor temperature wasn't known.")


# ---- endpoints ----

def test_post_reading_looks_up_outdoor_and_derives_the_rule(client, weather):
    weather["handler"] = open_meteo({"2026-10-20T23:00": 41.0})
    mid = make_mission()
    r = client.post(f"/missions/{mid}/heat-readings",
                    json={"indoor_f": 58, "taken_at": "2026-10-20T23:15:00Z"})
    assert r.status_code == 201
    assert r.json() == {"id": 1, "mission_id": mid, "taken_at": "2026-10-20T23:15:00Z",
                        "indoor_f": 58.0, "outdoor_f": 41.0, "source": "staff",
                        "required_f": 68, "violation": True}


def test_a_given_outdoor_is_used_without_a_lookup(client, weather):
    mid = make_mission()
    r = client.post(f"/missions/{mid}/heat-readings",
                    json={"indoor_f": 64, "outdoor_f": 56, "taken_at": "2026-10-20T23:15:00Z", "source": "seed"})
    assert r.json()["outdoor_f"] == 56.0 and r.json()["violation"] is False and r.json()["source"] == "seed"
    assert weather["requests"] == []


def test_weather_down_still_logs_the_reading(client):
    """By day that's 'can't tell' (null), not an error and not a false 'no violation'."""
    mid = make_mission()
    r = client.post(f"/missions/{mid}/heat-readings", json={"indoor_f": 58, "taken_at": "2026-10-20T18:00:00Z"})
    assert r.status_code == 201
    assert (r.json()["outdoor_f"], r.json()["required_f"], r.json()["violation"]) == (None, None, None)


def test_taken_at_is_stored_as_utc_and_a_naive_time_means_new_york(client):
    """A staffer typing 2026-10-20T19:00 means 7pm in New York, which is 23:00Z."""
    mid = make_mission()
    naive = client.post(f"/missions/{mid}/heat-readings",
                        json={"indoor_f": 58, "outdoor_f": 41, "taken_at": "2026-10-20T19:00"}).json()
    offset = client.post(f"/missions/{mid}/heat-readings",
                         json={"indoor_f": 58, "outdoor_f": 41, "taken_at": "2026-10-20T19:00:00-04:00"}).json()
    assert naive["taken_at"] == offset["taken_at"] == "2026-10-20T23:00:00Z"
    assert naive["required_f"] == 68
    assert client.post(f"/missions/{mid}/heat-readings",
                       json={"indoor_f": 58, "taken_at": "tuesday"}).status_code == 422


def test_taken_at_defaults_to_now(client):
    mid = make_mission()
    before = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    taken = client.post(f"/missions/{mid}/heat-readings", json={"indoor_f": 70, "outdoor_f": 40}).json()["taken_at"]
    assert before <= taken <= datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_unknown_mission_is_404(client):
    assert client.post("/missions/M-999/heat-readings", json={"indoor_f": 58}).status_code == 404
    assert client.get("/missions/M-999/heat-readings").status_code == 404
    assert client.get("/missions/nonsense/heat-readings").status_code == 404


def test_a_reading_broadcasts_the_mission(client):
    mid = make_mission()
    with client.websocket_connect("/ws") as ws:
        client.post(f"/missions/{mid}/heat-readings", json={"indoor_f": 58, "outdoor_f": 41})
        assert ws.receive_json() == {"type": "mission", "id": mid}


# ---- the tenant agent's tool ----

@pytest.fixture
def tool(client, monkeypatch):
    monkeypatch.setenv("VOICE_TOOL_SECRET", SECRET)

    def run(call_id: str, indoor_f: float) -> str:
        r = client.post("/voice/tools/log_heat_reading", json={"call_id": call_id, "indoor_f": indoor_f},
                        headers={"X-Voice-Secret": SECRET})
        assert r.status_code == 200, r.text  # failures are sentences
        return r.json()["result"]

    return run


def mission_call(mid: str) -> str:
    ts = "2026-10-20T23:00:00Z"
    with db.connect() as conn:
        pk = conn.execute(insert(db.calls).values(
            city_id=db.current_city_id(), started_at=ts, updated_at=ts,
            mission_id=db.mission_pk(mid), kind="heat_check")).inserted_primary_key[0]
    return db.call_id(pk)


def freeze(monkeypatch, iso: str):
    """Pin 'now' inside heat.py so the tool's reading falls on a chosen side of the rule."""
    fixed = datetime.fromisoformat(iso)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed

    monkeypatch.setattr(heat, "datetime", Frozen)


def test_tool_logs_a_voice_reading_and_names_the_violation(tool, client, weather, monkeypatch):
    freeze(monkeypatch, "2026-10-20T23:10:00+00:00")  # 7:10pm EDT
    weather["handler"] = open_meteo({"2026-10-20T23:00": 41.0})
    mid = make_mission()
    result = tool(mission_call(mid), 58)
    assert result == "Logged 58°F. That's a heat violation: the legal minimum right now is 68°F with 41°F outside."
    r = client.get(f"/missions/{mid}/heat-readings").json()["readings"][0]
    assert r["source"] == "voice" and r["taken_at"] == "2026-10-20T23:10:00Z" and r["violation"] is True


def test_tool_says_legal_when_it_is_legal(tool, monkeypatch):
    freeze(monkeypatch, "2026-10-21T03:00:00+00:00")  # 11pm EDT: 62 at night
    mid = make_mission()
    assert tool(mission_call(mid), 64) == (
        "Logged 64°F. That meets the legal minimum of 62°F right now, so it's not a violation.")


def test_tool_warm_day_is_not_a_violation(tool, weather, monkeypatch):
    freeze(monkeypatch, "2026-10-20T18:00:00+00:00")
    weather["handler"] = open_meteo({"2026-10-20T18:00": 60.0})
    assert tool(mission_call(make_mission()), 60) == (
        "Logged 60°F. It's 60°F outside, so the law doesn't require heat right now; that's not a violation.")


def test_tool_unknown_weather_by_day_does_not_guess(tool, monkeypatch):
    freeze(monkeypatch, "2026-10-20T18:00:00+00:00")
    assert "can't tell yet" in tool(mission_call(make_mission()), 60)


def test_tool_out_of_season(tool, monkeypatch):
    freeze(monkeypatch, "2026-07-15T03:00:00+00:00")
    assert "outside heat season" in tool(mission_call(make_mission()), 50)


def test_tool_on_a_call_without_a_mission_is_a_sentence_and_logs_nothing(tool, client):
    call_id = client.post("/calls", json={}).json()["id"]
    assert "isn't linked" in tool(call_id, 58)
    with db.connect() as conn:
        assert conn.execute(select(func.count()).select_from(db.heat_readings)).scalar() == 0


def test_tool_on_an_unknown_call_is_a_sentence(tool):
    assert tool("CALL-999", 58) == "I couldn't reach the case system right now, please try again later."


def test_tool_needs_the_secret(client, monkeypatch):
    monkeypatch.setenv("VOICE_TOOL_SECRET", SECRET)
    r = client.post("/voice/tools/log_heat_reading", json={"call_id": "CALL-1", "indoor_f": 58},
                    headers={"X-Voice-Secret": "wrong"})
    assert r.status_code == 401


# ---- the heat-check call ----

@pytest.fixture
def dial(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_TENANT_AGENT_ID", "agent-tenant")
    seen = []

    async def fake(agent_id, to_number, dynamic_variables, language=None):
        seen.append((agent_id, to_number, dynamic_variables, language))
        if fake.error:
            raise fake.error
        return "conv-123"

    fake.error = None
    fake.seen = seen
    monkeypatch.setattr(elevenlabs, "outbound_call", fake)
    return fake


def test_heat_check_call_dials_the_tenant_in_their_language(client, dial):
    mid = make_mission(phone="5551234567", language="es")
    r = client.post(f"/missions/{mid}/heat-check-call")
    assert r.status_code == 201
    call = r.json()
    assert (call["kind"], call["mission_id"], call["conversation_id"]) == ("heat_check", mid, "conv-123")
    # the agent's tools find the mission through call_id, so it must be the row we created
    [(agent, to, variables, language)] = dial.seen
    assert (agent, to, language) == ("agent-tenant", "5551234567", "es")
    assert (variables["call_id"], variables["mission_id"]) == (call["id"], mid)
    # one tenant agent serves heat checks and report-backs; purpose is how it tells them apart
    assert variables["purpose"] == "heat_check"
    assert client.get(f"/calls/{call['id']}").json()["kind"] == "heat_check"


def test_heat_check_call_broadcasts_the_call_and_the_mission(client, dial):
    mid = make_mission()
    with client.websocket_connect("/ws") as ws:
        call_id = client.post(f"/missions/{mid}/heat-check-call").json()["id"]
        assert ws.receive_json() == {"type": "call", "id": call_id}
        assert ws.receive_json() == {"type": "mission", "id": mid}


def test_not_configured_is_503_and_creates_no_call(client, monkeypatch, dial):
    """A call row for a call never placed would sit 'active' on the dashboard forever."""
    mid = make_mission()
    monkeypatch.delenv("ELEVENLABS_TENANT_AGENT_ID")
    assert client.post(f"/missions/{mid}/heat-check-call").status_code == 503
    assert dial.seen == [] and count_calls() == 0
    monkeypatch.setenv("ELEVENLABS_TENANT_AGENT_ID", "agent-tenant")
    dial.error = elevenlabs.NotConfigured("ELEVENLABS_API_KEY not set")  # raised inside the dial
    assert client.post(f"/missions/{mid}/heat-check-call").status_code == 503
    assert count_calls() == 0


def test_elevenlabs_error_is_502_and_creates_no_call(client, dial):
    mid = make_mission()
    dial.error = httpx.ConnectError("down")
    assert client.post(f"/missions/{mid}/heat-check-call").status_code == 502
    assert count_calls() == 0
    dial.error = None
    # and the next attempt works: the failed row didn't leave anything behind to trip on
    assert client.post(f"/missions/{mid}/heat-check-call").status_code == 201
    assert count_calls() == 1


def test_heat_check_call_unknown_mission_is_404(client, dial):
    assert client.post("/missions/M-999/heat-check-call").status_code == 404
    assert dial.seen == []
