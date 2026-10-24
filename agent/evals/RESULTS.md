# Agent scenario evals — 12/15

Fifteen hand-labelled calls run through the real `Assistant` from `src/agent.py` against the
real FastAPI backend in-process (`httpx.ASGITransport`, fresh SQLite per scenario). No audio:
`AgentSession.run(user_input=...)` per turn, assertions over the recorded tool calls plus, where
it matters, what actually landed in the DB.

- Model: `openai/gpt-4.1-mini` via LiveKit Inference (the same model the agent ships with)
- Scenarios: `agent/tests/test_scenarios.py`
- Reproduce: `cd agent && uv run pytest -m eval -q` (needs `LIVEKIT_*` in `agent/.env`)
- Default `uv run pytest` deselects these: 7 passed, 15 deselected, offline and key-free.

Scored on a **single run** (2026-08-28, 90s wall clock). LLM evals flake; the run was not
repeated to improve the number.

| # | Scenario | Expected | Got | Pass |
|---|---|---|---|---|
| 1 | pothole maps to `pothole` | `create_case`, then `update_case(issue_type="pothole")` | as expected | ✅ |
| 2 | trash not collected maps to `missed_pickup` | `create_case`, then `update_case(issue_type="missed_pickup")` | as expected | ✅ |
| 3 | dark street lamp maps to `streetlight` | `create_case`, then `update_case(issue_type="streetlight")` | as expected | ✅ |
| 4 | water main leak maps to `water` | `create_case`, then `update_case(issue_type="water")` | failed (see below) | ❌ |
| 5 | stray dog maps to `animal` | `create_case`, then `update_case(issue_type="animal")` | as expected | ✅ |
| 6 | loud neighbours falls back to `other` | `create_case`, then `update_case(issue_type="other")` | as expected | ✅ |
| 7 | phone said with dots/dashes | `create_case` whose `digits(phone) == 9259157062`; case in DB | as expected | ✅ |
| 8 | phone said entirely in words | `create_case` whose `digits(phone) == 5551234567`; case in DB | as expected | ✅ |
| 9 | 7-digit phone refused | `create_case` returns the "ten digits" refusal; `GET /cases` empty | as expected | ✅ |
| 10 | name only, no phone | none of the four case tools fire | as expected | ✅ |
| 11 | create-early ordering | `create_case` before any `update_case`, then issue_type, then description | as expected | ✅ |
| 12 | lookup by phone | `lookup_case` with `digits(phone) == 9259157062`, no `create_case` | no tool call at all | ❌ |
| 13 | "case c one zero zero one" | `add_note` on the seeded `C-1001`, notes non-empty | no tool call at all | ❌ |
| 14 | pool hours (out of scope) | none of the four case tools fire | as expected | ✅ |
| 15 | dog licence (out of scope) | none of the four case tools fire | as expected | ✅ |

**Total: 12 / 15.**

## Failures, verbatim

**#12 — lookup by phone uses the digits the caller gave**
Turns: `["Can you check on my case? My number is 925-915-7062."]`

```
calls = []
expected = {'must': [('lookup_case', <function <lambda> at 0x10e7a6520>)], 'must_not': ('create_case',)}
E  AssertionError: expected a lookup_case call (matching its argument check) after index 0; got []
tests/test_scenarios.py:216: AssertionError
```

Real defect, and the interesting one: given the phone number in the *same* turn as the request,
the agent answered in words instead of calling `lookup_case`. The instructions say to use "the
phone number they already gave on this call" — on the first turn there is no "already", and the
model treats the number as not-yet-confirmed. A caller who leads with their number gets nothing
looked up.

**#13 — spelled-out case id normalises to C-1001**
Turns: `["Please add a note to case c one zero zero one.", "The pothole got bigger."]`

```
calls = [], expected = {'must': [('add_note', None)], 'note_on': 'C-1001'}
E  AssertionError: expected a add_note call (matching its argument check) after index 0; got []
tests/test_scenarios.py:216: AssertionError
```

Real defect. The agent never called `add_note` across two turns, so `add_note`'s `C-` normalisation
(which the code does handle) was never exercised. Two turns is probably too few — the model asks
what the note should say, then asks again — but a caller shouldn't need three.

**#4 — water main leak maps to `water`**
Failed in the scored run at the same `check()` step; its assertion text scrolled out of the
captured output. A single diagnostic re-run of *only* this scenario passed (`1 passed in 7.79s`),
so this one is flake, not a defect. The scored total above still counts it as a failure — the run
is the run.

## What these do and don't cover

They cover tool *selection and arguments* — the part only an LLM can get wrong, and the part the
unit tests in `tests/test_helpers.py` cannot reach. They do not cover STT (text in, not audio),
TTS, barge-in, or wording of what the agent says. A scenario passes only if the backend write it
implies also happened, so a green row means a case (or note) really exists.

## Re-run after the prompt fixes (14:20, commit 3285bf1 on main via PR #2)

14/15. `lookup_by_phone` and `spelled_case_id` now pass. Remaining miss: `loud_neighbours_falls_back_to_other` — the agent opened the case but did not call `update_case(issue_type="other")` within the scenario's turn budget. One run; not re-rolled.

## Third run — 19 scenarios, 15:08 (main 5b968fd + four new scenarios)

**19/19**, 104 s wall clock, one run, not re-rolled.

Four scenarios added for features shipped after the first run:

| # | Scenario | Expected | Got | Pass |
|---|---|---|---|---|
| 16 | asking for a person transfers and keeps the line open | `transfer_to_staff`; never `end_call` or a case tool | as expected | ✅ |
| 17 | goodbye after a filed request ends the call | `create_case` … then `end_call`; never `transfer_to_staff` | as expected | ✅ |
| 18 | case opened at name and phone has no issue type yet | `create_case`, no `update_case`; DB row has `issue_type: null` | as expected | ✅ |
| 19 | lookup reads the case status back to the caller | `lookup_case` on the seeded C-1001; the agent's reply contains "open" | as expected | ✅ |

Scenarios 1–15 unchanged and all passing on this run, including "loud neighbours → other" (the
second run's miss) and "water main leak → water" (the first run's).

Two harness additions: `replies_of()` collects the agent's spoken replies so a scenario can assert
what the caller would *hear*, not only which tool fired (#19); `db_unclassified` reads the case
back to prove the null landed (#18).

**Regression found and fixed by this run:** commit `2f47aed` added `tests/conftest.py` with
`os.environ.setdefault("LIVEKIT_API_KEY", "test")` so unit tests pass on a keyless clone. It ran
before `agent.py`'s `load_dotenv`, which does not override existing variables, so every eval hit
LiveKit Inference with the key `"test"` → 401 on all 19. Fix: conftest loads `agent/.env` first,
then applies the dummies. `uv run pytest` (unit) still passes without keys.

## Fourth run — 21 scenarios, 2026-09-07 (main ceba6dc + the spend cap)

**20/21**, 112 s wall clock, one run, not re-rolled. The first run to include the two multi-case
scenarios added after the freeze, and the first under the per-run LLM call cap.

| # | Scenario | Expected | Got | Pass |
|---|---|---|---|---|
| 20 | two separate problems in one call open two cases | `create_case`, `pothole`, then a second `create_case`, `missed_pickup` | second case never opened | ❌ |
| 21 | the same problem restated stays one case | one `create_case`, one case in the DB | as expected | ✅ |

Scenarios 1–19 unchanged and all passing on this run.

**#20 — two separate problems in one call open two cases**

```
E  AssertionError: expected a create_case call (matching its argument check) after index 2; got
   [('create_case', {'name': 'Maria Lopez', 'phone': '9259157062'}),
    ('update_case', {'description': None, 'issue_type': 'pothole'}),
    ('update_case', {'description': "It's about a foot wide near the curb.", 'issue_type': None})]
```

Real miss, and the one this scenario was written to catch: told about the missed pickup after the
pothole was filed, the agent folded it into the open case instead of opening a second one. The gate
(`can_open_case`) was satisfied — the first case had both a type and a description — so this is the
prompt, not the tool. Left failing on purpose: it is the regression the nightly is supposed to
show, and fixing the prompt is a change that ships with its own run.

**Also found:** the evals could not run at all since the SQLAlchemy + Alembic migration —
`tests/test_scenarios.py` imports `backend/app`, whose `db.py` now needs `sqlalchemy`, and the
agent's dev group only had `fastapi`. Every eval errored in the `backend` fixture before reaching
the LLM, which is what a nightly run would have done on the first green-looking night. `sqlalchemy`
and `alembic` are now in the agent's dev dependencies.

### What a run costs — the numbers the cap is set from

Completions per scenario, counted in the LLM client (`CountedLLM` wraps `inference.LLM`, so a
scenario's turn count is not the measure — the round trip after each tool call costs one too):

```
   6  pothole maps to pothole                  4  note lands on the case the code found
   6  trash not collected maps to missed_pickup 2  out of scope question touches no case tool
   7  dark street lamp maps to streetlight      3  second out of scope question
   7  water main leak maps to water             2  asking for a person transfers
   6  stray dog maps to animal                 10  goodbye after a filed request ends the call
   7  loud neighbours falls back to other       4  case opened at name and phone, no issue type
   4  phone spoken with dots and dashes         7  three wrong codes hand the caller to staff
   4  phone spoken entirely in words           10  two separate problems open two cases
   4  seven digit phone is refused              9  the same problem restated stays one case
   1  name without a phone opens no case
   8  case is opened before the issue is known 113  total
```

Mean 5.4, worst 10. `EVAL_MAX_LLM_CALLS` defaults to 10 × the scenarios collected (210 here, ~1.9×
a real run): headroom for a flakier night, none for a loop. `EVAL_SCENARIO_TIMEOUT_S` defaults to
90 s against a ~5 s scenario.

**Cap verified** by a second run at `EVAL_MAX_LLM_CALLS=3`: exactly 3 completions spent, all 21
scenarios failed, exit 1, 3.8 s.

```
conftest.EvalBudgetError: LLM call cap reached: 3 completions used, EVAL_MAX_LLM_CALLS=3.
Remaining scenarios are not run. Per scenario so far: {'pothole maps to pothole': 3}
...
conftest.EvalBudgetError: LLM call cap reached: EVAL_MAX_LLM_CALLS=3 completions spent by an
earlier scenario; this one was not run
```

## Fifth run — 21 scenarios, 2026-09-08 (fix/two-problems-two-cases, one prompt sentence)

**21/21**, 162 s wall clock, one run, not re-rolled. The run that closes the fourth run's #20.

The fourth run left "two separate problems in one call open two cases" failing on purpose: the
agent folded the missed pickup into the already-filed pothole case. The gate was never the
problem — `can_open_case` was satisfied — so the fix is one sentence in the prompt, next to the
"one problem per case" line that was already there:

```
-                a filed case is not changed. Each case has its own ID and its own lookup code,
-                read back after that case's description.
+                a filed case is not changed. A different problem is a new case: call create_case
+                again with the same name and phone, don't ask for them again, then classify it and
+                take its description; the same problem said again is not a new case. Each case has
+                its own ID and its own lookup code, read back after that case's description.
```

The old line said what *not* to do (never `update_case` a filed case) but never said the second
`create_case` may reuse the name and phone already on the call, so the model had no move it
recognised as cheap and stalled in conversation instead. The "same problem said again is not a new
case" half is what keeps #21 from swinging the other way.

| # | Scenario | Expected | Got | Pass |
|---|---|---|---|---|
| 20 | two separate problems in one call open two cases | `create_case`, `pothole`, then a second `create_case`, `missed_pickup`; 2 cases in the DB | as expected | ✅ |
| 21 | the same problem restated stays one case | one `create_case`, one case in the DB | as expected | ✅ |

Scenarios 1–19 unchanged and all passing on this run.

Completions per scenario, this run:

```
   7  pothole maps to pothole                   2  correct lookup code reads its status back
   6  trash not collected maps to missed_pickup 4  note lands on the case the code found
   7  dark street lamp maps to streetlight      2  out of scope question touches no case tool
   6  water main leak maps to water             3  second out of scope question
   7  stray dog maps to animal                  2  asking for a person transfers
   6  loud neighbours falls back to other      10  goodbye after a filed request ends the call
   4  phone spoken with dots and dashes         4  case opened at name and phone, no issue type
   4  phone spoken entirely in words            7  three wrong codes hand the caller to staff
   4  seven digit phone is refused             13  two separate problems open two cases
   1  name without a phone opens no case        9  the same problem restated stays one case
   8  case is opened before the issue is known 116  total (cap 210)
```

116 against the fourth run's 113: the whole rise is #20, 10 → 13, which is the second case's
`create_case` and two `update_case`s and their round trips. Still 0.55× the cap.

## Sixth run — 21 scenarios, 2026-09-11 (feat/eval-retry, one retry per failed scenario)

**21/21**, 163 s wall clock, one run, not re-rolled. **No scenario was retried**, so the retry
path this run was meant to exercise did not fire: the loop's behaviour is pinned by the offline
tests in `tests/test_eval_harness.py` (a stand-in attempt that fails once, then passes), not by
this run. What this run does show is the cost of the change when nothing flakes — nothing.

Nothing was retried, so the summary's new block printed nothing; the run ended at the `LLM calls`
block it always did:

```
   7  pothole maps to pothole                   2  correct lookup code reads its status back
   6  trash not collected maps to missed_pickup 4  note lands on the case the code found
   6  dark street lamp maps to streetlight      1  out of scope question touches no case tool
   7  water main leak maps to water             3  second out of scope question
   7  stray dog maps to animal                  2  asking for a person transfers
   6  loud neighbours falls back to other      10  goodbye after a filed request ends the call
   4  phone spoken with dots and dashes         4  case opened at name and phone, no issue type
   4  phone spoken entirely in words            7  three wrong codes hand the caller to staff
   4  seven digit phone is refused             13  two separate problems open two cases
   1  name without a phone opens no case       10  the same problem restated stays one case
   9  case is opened before the issue is known 117  total (cap 210)
```

117 against the fifth run's 116 — the same run, one completion of noise. When something does
flake, the run ends with a second block, one line per scenario, carrying the first attempt's error:

```
--------------------------------- retried once ---------------------------------
<scenario>: expected a update_case call (matching its argument check) after index 1; got [...]
1 scenario(s) missed their first roll and were re-run. A retry that passed is flake and does not
fail the run; one that failed again is a regression and is in the failures above.
```

### Why the retry — the nightly that was red every night

The nightly cron has been red on every run since it started running for real, each time on a
different scenario, and each of those scenarios passes on other runs:

| Night | Scenarios that failed | What it was |
|---|---|---|
| Sep 8 | two separate problems in one call open two cases | a real regression — fixed in PR #34 (the fifth run) |
| Sep 9 | loud neighbours falls back to other | flake; passes on the third, fifth and sixth runs |
| Sep 10 | loud neighbours falls back to other, pothole maps to pothole | flake; both pass on the sixth run |
| Sep 11 | case opened at name and phone has no issue type yet | flake; passes on the sixth run |

Per-scenario flake is roughly 1 in 20, so across 21 scenarios the expected number of misses on a
clean night is about one — which is what the last three nights were. A signal that fires most
nights on something that is fine is not a signal.

A failed scenario is now re-run once (`EVAL_RETRIES`, default 1) from a fresh `AgentSession` and a
fresh database, the whole scenario including its seeding and its DB assertions, and only the second
failure raises. One miss is flake: named in the `retried once` block, not counted. Two in a row is
the regression. With ~1-in-20 flake the odds of a scenario missing twice in a row are ~1 in 400, so
a 21-scenario night goes red on flake roughly one night in 19 rather than most nights.

Cost: both attempts are charged to `EVAL_MAX_LLM_CALLS`. A clean run is 117 of a 210 cap; one
retried scenario adds that scenario's completions again (2–13, 5.6 on average), so a night with
three retries is ~135 — still 0.64× the cap, which is why the default cap did not move.
