"""The NYC heat rule (CONTRACT "### The heat rule", Housing Maintenance Code §27-2029).

This is what the caller agent quotes to a landlord, so a wrong answer here is the agent
asserting a violation that isn't one (or missing one that is). Every boundary the law draws
is pinned: 6:00am / 10:00pm, outdoor 55°F, Oct 1 / May 31, DST changeovers, and the UTC ->
New York conversion that decides which side of each boundary a stored "…Z" reading falls.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.heat import in_season, is_night, is_violation, required_f

NY = ZoneInfo("America/New_York")


def ny(*args) -> datetime:
    return datetime(*args, tzinfo=NY)


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ---- day vs night: the law switches minimums at 6:00am and 10:00pm local ----

@pytest.mark.parametrize("hour, minute, night", [
    (5, 59, True),   # last minute of the night window
    (6, 0, False),   # 6:00am is day: 68°F rule starts
    (21, 59, False),  # last minute of day
    (22, 0, True),   # 10:00pm is night: 62°F regardless of outdoor
    (0, 0, True),
    (12, 0, False),
])
def test_day_and_night_windows_switch_exactly_at_6am_and_10pm(hour, minute, night):
    assert is_night(ny(2026, 1, 15, hour, minute)) is night


def test_at_night_62_is_required_even_when_it_is_warm_outside():
    """The night minimum does not depend on the weather; a 60°F night still needs 62°F."""
    assert required_f(ny(2026, 1, 15, 22, 0), 70.0) == 62
    assert required_f(ny(2026, 1, 15, 5, 59), None) == 62
    assert is_violation(ny(2026, 1, 15, 22, 0), 61.9, 70.0) is True
    assert is_violation(ny(2026, 1, 15, 22, 0), 62.0, 70.0) is False


def test_by_day_68_is_required_only_when_outdoor_is_below_55():
    """'Below 55' is strict: at exactly 55°F outside the day minimum does not apply."""
    day = ny(2026, 1, 15, 19, 0)
    assert required_f(day, 54.9) == 68
    assert required_f(day, 55.0) is None
    assert is_violation(day, 60.0, 54.9) is True
    assert is_violation(day, 60.0, 55.0) is False  # nothing required, so no violation


def test_the_minimum_is_a_floor_not_a_target():
    """violation = indoor < required: exactly the minimum is legal."""
    day = ny(2026, 1, 15, 19, 0)
    assert is_violation(day, 68.0, 41.0) is False
    assert is_violation(day, 67.9, 41.0) is True


def test_6am_with_cold_outside_is_judged_by_the_day_rule():
    """At 5:59 a 65°F flat is legal (62 at night); at 6:00 with 40°F outside it is not."""
    assert is_violation(ny(2026, 1, 15, 5, 59), 65.0, 40.0) is False
    assert is_violation(ny(2026, 1, 15, 6, 0), 65.0, 40.0) is True


def test_10pm_switches_to_the_night_rule():
    """At 21:59 a 65°F flat with 40°F outside is a violation (68); at 22:00 it is legal (62)."""
    assert is_violation(ny(2026, 1, 15, 21, 59), 65.0, 40.0) is True
    assert is_violation(ny(2026, 1, 15, 22, 0), 65.0, 40.0) is False
    assert required_f(ny(2026, 1, 15, 22, 0), 40.0) == 62


# ---- unknown outdoor: can't tell by day, doesn't matter at night ----

def test_unknown_outdoor_by_day_means_we_cannot_tell():
    """By day the minimum is 68 or nothing depending on the weather; with no weather we must
    not claim a violation — or deny one."""
    day = ny(2026, 1, 15, 14, 0)
    assert required_f(day, None) is None
    assert is_violation(day, 50.0, None) is None


def test_unknown_outdoor_at_night_is_still_judged():
    """The night rule ignores outdoor, so a failed weather lookup doesn't lose a night violation."""
    assert is_violation(ny(2026, 1, 15, 23, 0), 58.0, None) is True


# ---- heat season: Oct 1 – May 31 inclusive, local time ----

@pytest.mark.parametrize("when, season", [
    (ny(2026, 9, 30, 23, 59), False),
    (ny(2026, 10, 1, 0, 0), True),
    (ny(2026, 5, 31, 23, 59), True),
    (ny(2026, 6, 1, 0, 0), False),
    (ny(2026, 7, 15, 12, 0), False),
    (ny(2026, 12, 31, 12, 0), True),
    (ny(2027, 1, 1, 0, 0), True),
])
def test_heat_season_runs_oct_1_through_may_31(when, season):
    assert in_season(when) is season


def test_out_of_season_nothing_is_required_and_nothing_is_a_violation():
    """Summer: a 50°F flat at 3am is not a heat violation, and unknown outdoor is False, not None."""
    summer_night = ny(2026, 7, 15, 3, 0)
    assert required_f(summer_night, 40.0) is None
    assert is_violation(summer_night, 50.0, 40.0) is False
    assert is_violation(ny(2026, 7, 15, 14, 0), 50.0, None) is False


def test_season_edges_judged_in_new_york_not_utc():
    """2026-06-01T02:00Z is still May 31, 10pm in New York: in season, night rule."""
    assert in_season(utc("2026-06-01T02:00:00Z")) is True
    assert is_violation(utc("2026-06-01T02:00:00Z"), 60.0, None) is True
    # 2026-10-01T03:00Z is Sep 30, 11pm in New York: not yet in season
    assert in_season(utc("2026-10-01T03:00:00Z")) is False
    assert in_season(utc("2026-10-01T04:00:00Z")) is True  # Oct 1, midnight EDT


# ---- UTC -> New York ----

def test_a_utc_reading_is_judged_at_its_new_york_time():
    """02:30Z in October is 10:30pm the previous evening in New York (EDT, UTC-4): night, 62°F.
    Read as UTC it would be 2:30am — also night — so check the day side too: 01:30Z is 9:30pm."""
    assert required_f(utc("2026-10-21T02:30:00Z"), 40.0) == 62
    assert required_f(utc("2026-10-21T01:30:00Z"), 40.0) == 68  # 21:30 local, day rule
    assert required_f(utc("2026-10-21T14:00:00Z"), 40.0) == 68  # 10am EDT


def test_winter_readings_use_est_not_edt():
    """In January New York is UTC-5: 03:00Z is 10pm (night), 02:59Z is 9:59pm (day)."""
    assert is_night(utc("2026-01-15T03:00:00Z")) is True
    assert is_night(utc("2026-01-15T02:59:00Z")) is False


def test_spring_forward_day_uses_the_new_offset():
    """2026-03-08: clocks jump 2am EST -> 3am EDT. 10:00Z is 6:00am EDT (day); a fixed UTC-5
    offset would call it 5am (night) and apply the wrong minimum."""
    assert is_night(utc("2026-03-08T09:59:00Z")) is True   # 5:59am EDT
    assert is_night(utc("2026-03-08T10:00:00Z")) is False  # 6:00am EDT
    assert required_f(utc("2026-03-08T10:00:00Z"), 40.0) == 68


def test_fall_back_day_uses_the_old_offset_after_the_change():
    """2026-11-01: 2am EDT -> 1am EST. 10:59Z is 5:59am EST (night), 11:00Z is 6:00am (day);
    a fixed UTC-4 offset would put 10:00Z at 6am."""
    assert is_night(utc("2026-11-01T10:59:00Z")) is True
    assert is_night(utc("2026-11-01T11:00:00Z")) is False
    assert is_night(utc("2026-11-01T10:00:00Z")) is True  # 5am EST, not 6am EDT


def test_naive_datetimes_are_refused():
    """A naive datetime has no instant; guessing the zone would silently move the 10pm line."""
    with pytest.raises(ValueError):
        required_f(datetime(2026, 1, 15, 22, 0), 40.0)


def test_aware_non_utc_inputs_are_converted_too():
    assert is_night(datetime(2026, 1, 16, 3, 0, tzinfo=timezone.utc)) is True
