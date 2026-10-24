"""The eval harness itself: how many attempts a scenario gets, and what the loop does.

A harness bug here is silent in a way a scenario bug is not -- a misread EVAL_RETRIES or a
retry that reuses the first attempt's database turns a red nightly green and nobody finds
out. None of this touches the network: the scenario's one attempt is stood in for.
"""
import pytest
import test_scenarios as ts
from conftest import attempts_from_env
from test_scenarios import backend  # noqa: F401  -- used as a fixture below


def test_attempts_default_to_one_retry():
    """Unset is the nightly's case: one roll plus one, so a single miss is not a red run."""
    assert attempts_from_env({}) == 2


def test_attempts_treat_empty_as_unset():
    """evals.yml passes `${{ vars.EVAL_RETRIES }}`, which is "" when the repo var is unset."""
    assert attempts_from_env({"EVAL_RETRIES": ""}) == 2
    assert attempts_from_env({"EVAL_RETRIES": " "}) == 2


def test_attempts_zero_restores_the_strict_single_roll():
    """0 has to survive the fallback: it is the only way back to one-miss-fails-the-run."""
    assert attempts_from_env({"EVAL_RETRIES": "0"}) == 1


def test_attempts_honour_more_retries():
    """A flakier model can be given more rolls without a code change."""
    assert attempts_from_env({"EVAL_RETRIES": "2"}) == 3


def test_attempts_refuse_a_negative():
    """-1 would mean zero attempts: the scenario never runs and the run is green for nothing."""
    with pytest.raises(ValueError):
        attempts_from_env({"EVAL_RETRIES": "-1"})


# The loop itself never runs offline in a real eval -- a retry only happens when a live
# model misses -- so these drive it with a stand-in attempt. They are unmarked on purpose:
# nothing here reaches the network, and the loop is too easy to get quietly wrong.


async def test_a_failed_scenario_is_re_run_from_an_empty_database(
    backend,  # noqa: F811
    eval_budget,
    monkeypatch,
    tmp_path,
):
    """The retry must not inherit the first attempt's rows: a scenario asserting two cases
    would pass on a retry that opened one, and the flake would be recorded as a pass."""
    rows_at_start = []

    async def attempt(turns, expected, app, budget):
        http = ts.sync_client(app)
        rows_at_start.append(len(http.get("/cases").json()))
        http.post(
            "/cases", json={"name": "Maria Lopez", "phone": "9259157062"}
        )  # the case the first attempt would have filed
        if len(rows_at_start) == 1:
            raise AssertionError("expected a create_case call; got []")

    monkeypatch.setattr(ts, "attempt_scenario", attempt)
    await ts.test_scenario("fake", [], {}, backend, eval_budget, monkeypatch, tmp_path)
    assert rows_at_start == [0, 0]
    assert eval_budget.retried == {"fake": "expected a create_case call; got []"}


async def test_a_second_failure_is_the_one_that_raises(
    backend,  # noqa: F811
    eval_budget,
    monkeypatch,
    tmp_path,
):
    """The point of the retry is that it still fails: a scenario the agent misses twice is
    a regression, and a harness that swallowed it would make the nightly worthless."""
    tries = []

    async def always_missing(turns, expected, app, budget):
        tries.append(1)
        raise AssertionError("still missing")

    monkeypatch.setattr(ts, "attempt_scenario", always_missing)
    with pytest.raises(AssertionError, match="still missing"):
        await ts.test_scenario("fake", [], {}, backend, eval_budget, monkeypatch, tmp_path)
    assert len(tries) == eval_budget.attempts == 2
