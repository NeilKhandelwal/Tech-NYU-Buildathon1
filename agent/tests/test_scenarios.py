"""Offline scenario evals: does the real agent call the right tools on the right turns?

The unit tests cover the pure helpers; these cover the part that only an LLM can get
wrong -- whether the model maps what a caller says onto the tool surface at all. Every
scenario is hand-labelled: `turns` is what a resident says, `expected` is the tool
behaviour a case worker would consider correct. Tools run against the real backend
in-process (httpx.ASGITransport over the FastAPI app, fresh SQLite per test), so a
scenario also proves the case really landed in the DB.

Marked `eval` and deselected by default -- they hit a live LLM and need LIVEKIT_* keys.
Run with: uv run pytest -m eval -q

Spend is capped per run: `EVAL_MAX_LLM_CALLS` completions in total and
`EVAL_SCENARIO_TIMEOUT_S` seconds per scenario (see `conftest.EvalBudget`).

A failed scenario is re-run once before it counts (`EVAL_RETRIES`, default 1): one miss is
this model's usual flake, two in a row is a regression. Retries are named in the summary.
"""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from livekit.agents import AgentSession, inference

AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR / "src"))
sys.path.insert(0, str(AGENT_DIR.parent / "backend"))

import agent as agent_mod  # noqa: E402
from agent import Assistant, digits  # noqa: E402

MODEL = "openai/gpt-4.1-mini"
CASE_TOOLS = ("create_case", "update_case", "lookup_case", "add_note")


# --- expectations -------------------------------------------------------------------
# `must` is an ordered subsequence: the named calls have to occur in this relative order,
# other calls may interleave. `must_not` names tools that may never fire.


def issue(kind):
    return ("update_case", lambda a: a.get("issue_type") == kind)


def phone_is(number):
    return ("create_case", lambda a: digits(a.get("phone", "")) == number)


NAME_PHONE = ["Hi, my name is Maria Lopez.", "It's 925.915.7062.", "Yes, that's right."]

SCENARIOS = [
    (
        "pothole maps to pothole",
        [*NAME_PHONE, "There's a huge pothole on Elm Street."],
        {"must": [("create_case", None), issue("pothole")]},
    ),
    (
        "trash not collected maps to missed_pickup",
        [*NAME_PHONE, "My trash wasn't collected on Tuesday."],
        {"must": [("create_case", None), issue("missed_pickup")]},
    ),
    (
        "dark street lamp maps to streetlight",
        [*NAME_PHONE, "The street lamp outside my house has been dark for a week."],
        {"must": [("create_case", None), issue("streetlight")]},
    ),
    (
        "water main leak maps to water",
        [*NAME_PHONE, "Water is gushing out of a broken main on Third Avenue."],
        {"must": [("create_case", None), issue("water")]},
    ),
    (
        "stray dog maps to animal",
        [*NAME_PHONE, "There's a stray dog running loose in the park."],
        {"must": [("create_case", None), issue("animal")]},
    ),
    (
        "loud neighbours falls back to other",
        [*NAME_PHONE, "My neighbours are being really loud every night."],
        {"must": [("create_case", None), issue("other")]},
    ),
    (
        "phone spoken with dots and dashes",
        ["I'm Dan Reed.", "Nine two five dot nine one five dash seven zero six two.", "Correct."],
        {"must": [phone_is("9259157062")], "db_phone": "9259157062"},
    ),
    (
        "phone spoken entirely in words",
        [
            "This is Ana Diaz.",
            "My number is five five five, one two three, four five six seven.",
            "Yes.",
        ],
        {"must": [phone_is("5551234567")], "db_phone": "5551234567"},
    ),
    (
        "seven digit phone is refused and files nothing",
        ["I'm Sam Cole.", "It's 915-7062.", "Yes that's it."],
        {
            "must": [("create_case", None)],
            "output_contains": "ten",
            "db_empty": True,
        },
    ),
    (
        "name without a phone does not open a case",
        ["Hi, I'd like to report something. My name is Tom Blake."],
        {"must_not": CASE_TOOLS},
    ),
    (
        "case is opened before the issue is known",
        [*NAME_PHONE, "There's a pothole on Elm Street.", "It's about a foot wide near the curb."],
        {
            "must": [
                ("create_case", None),
                issue("pothole"),
                ("update_case", lambda a: bool(a.get("description"))),
            ],
            "create_before_update": True,
        },
    ),
    (
        "correct lookup code finds the case and reads its status back",
        ["Can you check on my case? My code is {code}."],
        {
            "must": [("lookup_case", None)],
            "must_not": ("create_case",),
            "seed_case": "C-1001",
            "reply_contains": "open",
        },
    ),
    (
        "note lands on the case the code found",
        ["I need to add something to my case. The code is {code}.", "The pothole got bigger."],
        {
            "must": [("lookup_case", None), ("add_note", None)],
            "note_on": "C-1001",
        },
    ),
    (
        "out of scope question touches no case tool",
        ["What time does the public pool open on Saturday?"],
        {"must_not": CASE_TOOLS},
    ),
    (
        "second out of scope question touches no case tool",
        ["Hi there.", "How much is a dog licence and where do I buy one?"],
        {"must_not": CASE_TOOLS},
    ),
    # --- added after warm transfer (#15), end_call, nullable issue_type (5b968fd) ---
    (
        "asking for a person transfers and keeps the line open",
        ["I don't want to talk to a robot. Put me through to a real person about my water bill."],
        {"must": [("transfer_to_staff", None)], "must_not": ("end_call", *CASE_TOOLS)},
    ),
    (
        "goodbye after a filed request ends the call",
        [*NAME_PHONE, "There's a pothole on Elm Street.", "About a foot wide by the curb.",
         "No, that's everything. Thanks, bye."],
        {"must": [("create_case", None), ("end_call", None)], "must_not": ("transfer_to_staff",)},
    ),
    (
        "case opened at name and phone has no issue type yet",
        NAME_PHONE,
        {"must": [("create_case", None)], "must_not": ("update_case",), "db_unclassified": True},
    ),
    (
        "three wrong codes hand the caller to staff instead of the case",
        ["Any update on my case? The code is stone cloud river.",
         "Maybe it was cloud stone river.", "Try river stone cloud then."],
        {"must": [("lookup_case", None), ("transfer_to_staff", None)]},
    ),
    # --- added after the freeze: one call, many cases ---
    (
        "two separate problems in one call open two cases",
        [*NAME_PHONE, "There's a huge pothole on Elm Street.", "It's about a foot wide near the curb.",
         "Also, my trash wasn't collected on Tuesday.", "They skipped the whole street."],
        {
            "must": [("create_case", None), issue("pothole"),
                     ("create_case", None), issue("missed_pickup")],
            "db_cases": 2,
        },
    ),
    (
        "the same problem restated stays one case",
        [*NAME_PHONE, "There's a huge pothole on Elm Street.", "It's about a foot wide near the curb.",
         "Like I said, that pothole on Elm Street is really bad."],
        {"must": [("create_case", None), issue("pothole")], "db_cases": 1},
    ),
]


# --- harness ------------------------------------------------------------------------


def fresh_db(tmp_path, monkeypatch, attempt):
    """An empty database for one attempt at a scenario.

    Per attempt, not per test: a retry re-runs the whole scenario, and the rows the first
    attempt wrote would answer for it -- `db_cases: 2` passes on a retry that opened one.
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / f'evals-{attempt}.db'}")
    from app import db

    db.reset_engine()  # the cached engine still points at whatever ran before this test
    db.init_db()  # ASGITransport does not run the lifespan that normally does this


@pytest.fixture
def backend(tmp_path, monkeypatch):
    """The real FastAPI app on a throwaway DB, wired into the agent's httpx calls.

    Stubbing the backend would let a scenario "pass" while writing nothing; running the
    real app means a green scenario also means the case exists.
    """
    fresh_db(tmp_path, monkeypatch, 0)
    from app import main

    transport = httpx.ASGITransport(app=main.app)
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs.setdefault("base_url", agent_mod.BACKEND)
        return real_client(*args, transport=transport, **kwargs)

    # patch the name inside agent.py, not httpx itself: the LLM client is also httpx,
    # and replacing httpx.AsyncClient globally would route inference at the backend too
    monkeypatch.setattr(agent_mod, "httpx", SimpleNamespace(AsyncClient=client))
    return main.app


def sync_client(app):
    """Same app, sync, for seeding and for reading the DB back after the run."""
    from fastapi.testclient import TestClient

    return TestClient(app)


def calls_of(results):
    """Flatten every RunResult into (tool_name, arguments, output) in chronological order."""
    out = []
    pending = {}
    for r in results:
        for ev in r.events:
            if ev.type == "function_call":
                try:
                    args = json.loads(ev.item.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                pending[ev.item.call_id] = [ev.item.name, args, ""]
                out.append(pending[ev.item.call_id])
            elif ev.type == "function_call_output":
                if ev.item.call_id in pending:
                    pending[ev.item.call_id][2] = str(ev.item.output)
    return [tuple(c) for c in out]


def replies_of(results):
    """Every assistant message the agent produced, in order (what the caller would hear)."""
    out = []
    for r in results:
        for ev in r.events:
            item = getattr(ev, "item", None)
            if ev.type == "message" and getattr(item, "role", None) == "assistant":
                out.append(item.text_content or "")
    return out


def check(calls, expected):
    """Turn a hand-labelled expectation into asserts with messages that name the miss."""
    seen = [(n, a) for n, a, _ in calls]

    i = 0
    for name, pred in expected.get("must", []):
        for j in range(i, len(calls)):
            if calls[j][0] == name and (pred is None or pred(calls[j][1])):
                i = j + 1
                break
        else:
            raise AssertionError(
                f"expected a {name} call (matching its argument check) after index {i}; got {seen}"
            )

    for name in expected.get("must_not", ()):
        assert name not in [n for n, _ in seen], f"{name} must not fire; got {seen}"

    if expected.get("create_before_update"):
        names = [n for n, _ in seen]
        assert "create_case" in names and "update_case" in names, f"got {seen}"
        assert names.index("create_case") < names.index("update_case"), (
            f"create_case must fire before any update_case; got {names}"
        )

    if "output_contains" in expected:
        needle = expected["output_contains"]
        assert any(needle in out for _, _, out in calls), (
            f"no tool output contained {needle!r}; got {[o for _, _, o in calls]}"
        )


class CountedLLM(inference.LLM):
    """The shipped inference client, with every completion charged to the run's budget.

    Subclassing is the seam that cannot be missed: whatever asks for a completion -- a
    turn, the round trip after a tool call, the hang-up summary -- goes through `chat()`.
    """

    def __init__(self, budget, scenario, **kwargs):
        super().__init__(**kwargs)
        self._budget = budget
        self._scenario = scenario

    def chat(self, **kwargs):
        self._budget.spend(self._scenario)  # raises instead of making the call, when over
        return super().chat(**kwargs)


def count_llm_calls(monkeypatch, budget, scenario):
    """Point the agent's own LLM at the budget.

    `Assistant()` constructs its own `inference.LLM` inside agent.py and that one wins
    over `AgentSession(llm=...)`, so the class the agent constructs is the only seam
    that sees the calls the scenario actually pays for.
    """
    monkeypatch.setattr(
        agent_mod.inference, "LLM", lambda **kw: CountedLLM(budget, scenario, **kw)
    )


async def run_scenario(turns, budget):
    session = AgentSession(llm=inference.LLM(model=MODEL))
    async with session:
        await session.start(Assistant())
        results = []
        for t in turns:
            results.append(await session.run(user_input=t))
            budget.raise_if_spent()  # the session may swallow the error from chat(); this is loud
        return results


async def attempt_scenario(turns, expected, app, budget):
    """One roll of a scenario: seed it, run the turns, and check everything it claims.

    Every assert lives in here rather than in the test, so a retry re-runs all of them --
    the database checks flake the same way the tool-call ones do.
    """
    http = sync_client(app)
    if expected.get("note_on") or expected.get("seed_case"):
        # a note needs a case to hang off; seed it through the backend's own endpoint
        seeded = http.post(
            "/cases",
            json={
                "name": "Maria Lopez",
                "phone": "9259157062",
                "issue_type": "pothole",
                "description": "Pothole on Elm St",
            },
        )
        want = expected.get("note_on") or expected["seed_case"]
        assert seeded.status_code == 201 and seeded.json()["id"] == want
        # the code is freshly generated per run, so the turns carry a {code} placeholder
        turns = [t.format(code=seeded.json()["lookup_code"].replace("-", " ")) for t in turns]

    try:
        results = await asyncio.wait_for(run_scenario(turns, budget), budget.timeout_s)
    except TimeoutError:
        # a hung LLM call would otherwise hold a CI runner for the length of the job
        pytest.fail(f"scenario ran past EVAL_SCENARIO_TIMEOUT_S={budget.timeout_s:g}s")
    calls = calls_of(results)
    check(calls, expected)

    if expected.get("db_empty"):
        assert http.get("/cases").json() == [], (
            f"a refused phone number must leave no case behind; tool calls: {calls}"
        )
    if "db_cases" in expected:
        # one problem per case: a second problem must open a second case, a restated one must not
        stored = http.get("/cases").json()
        assert len(stored) == expected["db_cases"], (
            f"expected {expected['db_cases']} case(s), got {[c['id'] for c in stored]}; tool calls: {calls}"
        )
    if expected.get("db_phone"):
        stored = http.get("/cases", params={"phone": expected["db_phone"]}).json()
        assert stored, f"no case stored under {expected['db_phone']}; tool calls: {calls}"
    if expected.get("note_on"):
        notes = http.get(f"/cases/{expected['note_on']}").json()["notes"]
        assert notes.strip(), f"add_note left {expected['note_on']} without notes; calls: {calls}"
    if expected.get("db_unclassified"):
        # nullable issue_type: a case opened at name+phone must be stored as "not classified yet"
        stored = http.get("/cases").json()
        assert len(stored) == 1 and stored[0]["issue_type"] is None, f"expected one unclassified case; got {stored}"
    if expected.get("reply_contains"):
        said = " ".join(replies_of(results)).lower()
        assert expected["reply_contains"] in said, f"agent never said {expected['reply_contains']!r}; said: {said!r}"


@pytest.mark.eval
@pytest.mark.parametrize(
    "name,turns,expected", SCENARIOS, ids=[s[0].replace(" ", "_") for s in SCENARIOS]
)
async def test_scenario(name, turns, expected, backend, eval_budget, monkeypatch, tmp_path):
    """Each scenario encodes a case worker's judgement of what should have been filed.

    A wrong issue_type or a case opened without a phone is a real intake defect, not a
    style difference, so these must fail when the agent stops behaving that way.

    One failure is not that, though: the model misses a scenario about 1 in 20 runs, so a
    single roll made the nightly red most nights on a different scenario each time. A
    failed scenario is run again from a fresh session and a fresh database and only the
    second failure raises; the first is named in the terminal summary instead. Both
    attempts are charged to the same cap. `EVAL_RETRIES=0` restores the single roll.
    """
    eval_budget.raise_if_spent()  # an earlier scenario blew the cap: spend nothing more
    count_llm_calls(monkeypatch, eval_budget, name)
    for attempt in range(eval_budget.attempts):
        if attempt:
            fresh_db(tmp_path, monkeypatch, attempt)  # the retry must not see the first rows
        try:
            await attempt_scenario(turns, expected, backend, eval_budget)
            return
        except AssertionError as failure:
            # only what the scenario judged: a blown cap or a timeout is not flake to re-roll
            if attempt == eval_budget.attempts - 1:
                raise
            eval_budget.note_retry(name, failure)
            eval_budget.raise_if_spent()  # the retry spends like any other attempt
