# EffiGov — voice intake → cases → dashboard

A voice intake line for local-government service requests, with a live case desk.

## What it is

A resident calls the City services line. A LiveKit voice agent answers, takes their name and
phone number, opens a service request while they are still explaining it, classifies the issue,
and reads back a case ID plus a three-word lookup code. A second, unrelated problem on the same
call opens its own case, with its own ID and code. When the resident calls back and says their
three words, the agent reads the current status. The phone number is discovery; the code is
authentication, so knowing a number or a case ID gets you nothing.

Staff watch the same call in a Next.js dashboard: the call appears the moment it starts, the
transcript streams line by line, the case fields fill in mid-call, and the new case lands at the
top of the list — no refresh. Staff change status, add notes, and see an audit trail of every
change with who made it (`staff` vs `voice`). A caller who asks for a person flags the call
`needs_person` and the line stays open.

## Architecture

```
 mic ──► agent/  (LiveKit Agents, dev mode)            dashboard/ (Next.js) ◄── Chrome
            │  httpx: POST /cases, /calls, /calls/{id}/transcript      ▲  fetch + WS /ws
            ▼                                                          │
         backend/  (FastAPI + SQLAlchemy Core)    ──── broadcast {type,id} ┘
         tables: cases · calls · call_cases · transcript · case_events
```

Three processes, one HTTP contract between them ([docs/CONTRACT.md](docs/CONTRACT.md)). The agent
only ever talks to the backend over HTTP, so the dashboard and the agent were built in parallel
and neither imports the other.

**Real-time.** After every write the backend pushes `{"type": "case"|"call"|"transcript", "id": ...}`
on `WS /ws`. The socket carries no payload; clients refetch what they are showing. Refetch is
idempotent, so duplicate or out-of-order frames cannot corrupt UI state. There is one socket per
page, shared by everything on it — the page's own data and the nav's Live/Polling dot read the same
connection. The 2-second poll is a fallback: it exists only while that socket is down. Every open,
including a reconnect, refetches once, so an outage costs one catch-up fetch and nothing else.

**Voice.** LiveKit Inference for STT (AssemblyAI), LLM (`openai/gpt-4.1-mini`, chosen for reliable
tool calls against a strict `issue_type` enum), and TTS (Fish Audio) — one LiveKit Cloud key, no
other provider accounts. Six function tools: `create_case`, `update_case`, `lookup_case`,
`add_note`, `transfer_to_staff` (flags the call `needs_person` and keeps the line open for staff),
`end_call`. Every tool swallows backend errors and says so to the caller instead of crashing the
call; if the backend is down at call start, the call still works, just without the dashboard.

## Data model

**Cases** (`cases`). One service request. The row id is an integer primary key; the public id
is `"C-" + (1000 + id)`, derived in the API layer, so the first case is `C-1001` and the agent has a short number to read
aloud. `phone` is stored digits-only. `issue_type` is one of `missed_pickup`, `pothole`,
`streetlight`, `water`, `animal`, `other` — and is **null until classified**, because the agent
opens the case before it knows the type. `status` is `open | in_progress | resolved` and only staff
set it. `notes` is not a column: it is derived from the case's `note` events, joined oldest first
(see **Notes** below).

**Calls** (`calls`). One voice session, public id `"CALL-" + id`. A call is not a case: one case
collects many calls (the report, then the follow-up), and a lookup-only call should not create a
junk case. A call carries `status` (`active | needs_person | ended`), `room` (the LiveKit room, so
the browser page can find its own call), `summary` (written by the agent at hang-up), and
`transfer_reason`. `case_id` is only a **cursor** — the case the agent is working right now.
Ending a call stamps `ended_at`; a PATCH that tries to revive an ended call gets a 409.

**Call ↔ case links** (`call_cases`). The join table is the truth about every case a call touched:
`(call_id, case_id)` primary key, `how` = `created | looked_up`, and `linked_at`. Call JSON exposes
`case_ids` in link order alongside the `case_id` cursor. Linking is idempotent — `POST
/calls/{id}/cases` returns 201 for a new link, 200 when it already existed — so a retry after a
transient failure cannot duplicate anything.

**Audit** (`case_events`). Append-only. `POST /cases` writes a `created` row; a PATCH writes one row
per field that actually changed (re-sending the same value writes nothing); linking writes
`call_linked`; a successful code lookup writes `looked_up`; a note writes `note`. `source` comes from the `X-Source`
header — `staff` by default, `voice` from the agent, `seed` for the seeded rows. Timestamps are
second-resolution, so ordering relies on `ORDER BY id`.

**Notes.** A note is a `case_events` row with `field="note"` and the text in `new_value`, so it has
its own source and timestamp. `POST /cases/{id}/notes {text}` appends one — a single write, so two
people adding a note in the same second no longer overwrite each other. `Case.notes` is still in the
JSON, derived from those rows; `PATCH /cases/{id}` refuses `notes` with a 422 rather than ignoring it.

**Storage.** `DATABASE_URL` picks the database — unset means a SQLite file at `backend/cases.db`, so
a local clone needs no infrastructure; compose and CI run `postgresql+psycopg://…` against
`postgres:16`. The schema lives in Alembic migrations under `backend/migrations/`, applied by
`alembic upgrade head` at app startup and in the Docker entrypoint; nothing else creates or alters a
table, and a database written before those migrations is refused at startup rather than half-upgraded
in place. `CITY_ID` (default `1`, the seeded city) names the city this deployment serves: every read
is filtered to it and every write records it. Foreign keys are on (SQLite gets `PRAGMA foreign_keys=ON` per connection), so the database
itself rejects a call linked to a case that does not exist, and `lookup_code` is unique. Every row also carries a `city_id`,
defaulted to the one seeded city — internal for now, and not in any response.

**Reading only what changed.** `GET /cases?since=<ISO>` and `GET /calls?since=<ISO>` return rows
written at or after the cursor; calls carry an `updated_at` that every write to the call bumps,
transcript lines included. Inclusive on purpose — timestamps are second-resolution, so a strict
comparison would silently drop anything written in the cursor's own second. A `since` that is not a
timestamp is a 422, not an empty list.

**Lookup codes.** Three words drawn from a 300-word list (27 million combinations), generated on
case creation and checked for collisions against the table. `POST /cases` is the only response that
carries `lookup_code`; the `Case` model has no such field, so it can never leak from a read.
`GET /cases/lookup?code=` normalizes what the caller said — lowercase, split on spaces, commas and
dashes, drop the fillers `and` and `dash` — so "Blue and River dash Maple" matches `blue-river-maple`.
Unknown and malformed codes get the same 404 (`no case for that code`) so the endpoint is not an
oracle, and five wrong codes on one `X-Call-Id` returns 429.

## Run locally

Docker Compose is coming in Phase 1; today it is three terminals.

**Prereqs:** [uv](https://docs.astral.sh/uv/) (installs Python 3.13 itself), Node 20.9+, and a
LiveKit Cloud project for the `LIVEKIT_*` keys (Settings → Keys). Without the keys, everything
except the voice call works.

```sh
# 1. backend (http://localhost:8000, SQLite file backend/cases.db — override with DATABASE_URL=<url>)
cd backend && cp .env.example .env    # LIVEKIT_* (needed only for the browser call's /token)
uv sync && uv run python -m scripts.seed && uv run uvicorn app.main:app --reload --port 8000
# 2. dashboard (http://localhost:3000 — open it at localhost, not 127.0.0.1, for CORS; backend URL from NEXT_PUBLIC_API_URL, default http://localhost:8000)
cd dashboard && npm install && npm run dev
# 3. voice agent worker — joins every browser call (LiveKit Cloud dispatch)
cd agent && cp .env.example .env      # LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET
uv sync && uv run python src/agent.py download-files   # once: turn-detector / VAD models
uv run python src/agent.py dev
```
- Env vars: `NEXT_PUBLIC_API_URL` (dashboard → backend, default `http://localhost:8000`), `CORS_ORIGINS` (backend, comma-separated, default `http://localhost:3000`) — both listed in the matching `.env.example` — and `DATABASE_URL` (backend; unset means the SQLite file, `postgresql+psycopg://…` for Postgres). The old `CASES_DB=<path>` still works as a SQLite-only alias and prints a deprecation line. Migrations run themselves at startup; `uv run alembic upgrade head` applies them by hand.
- Staff login: set `AUTH_SECRET` (`openssl rand -base64 32`) and `STAFF_USERS="name:hash,..."` in `dashboard/.env.local`, where each hash comes from `cd dashboard && npm run hash-password -- <password>`. **Dev-only shortcut:** leave `STAFF_USERS` unset and dashboard auth is disabled entirely — every route is open, the nav shows nobody, and the server logs one warning at startup — so a fresh clone and `docker compose up` still work with nothing configured. Never deploy with it unset.

Then open http://localhost:3000/call and press **Start call** (Chrome asks for the mic once).

Terminal-only alternative for the voice side: `uv run python src/agent.py console` talks through
your mic with no browser.

Reset to a clean state at any time: `cd backend && uv run python -m scripts.reset_demo` — wipes all
tables and reseeds the 3 sample cases, printing each case's lookup code.

### Run with docker

Local dev only (the three-terminal path above is still the primary one). Needs Docker Desktop / Engine with the Compose plugin.

```sh
docker compose up --build            # postgres + backend :8000 (migrated and seeded) + dashboard :3000
docker compose --profile voice up    # ...plus the LiveKit agent worker (needs agent/.env)
docker compose down                  # stop; add -v to also drop the postgres volume
```

Compose runs `postgres:16` on the named volume `pg-data`; the backend waits for it to be healthy, applies the migrations, and seeds the 3 sample cases only when the `cases` table is empty, so restarts leave data alone and `down -v` resets it. Outside compose the backend still defaults to SQLite and needs nothing running. The dashboard runs `next dev` with `dashboard/` bind-mounted, so edits hot-reload. The agent service reads `agent/.env` (`LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`) and talks to the backend over the compose network.

### CI

`.github/workflows/ci.yml` runs on every push to `main` and every PR: backend pytest twice — once on SQLite and once against a `postgres:16` service container, the same suite both times — agent pytest (unit tests only -- the `eval` marker stays deselected and no LiveKit keys are needed), and dashboard `npm run lint` + `npm run build`, in parallel.

`.github/workflows/evals.yml` runs the LLM scenario evals (`pytest -m eval`) on a nightly cron and on manual dispatch. It needs three repository secrets -- **`LIVEKIT_URL`**, **`LIVEKIT_API_KEY`**, **`LIVEKIT_API_SECRET`** -- and skips with a notice if they are not set. Set them once, from the `agent/.env` that already runs the evals locally:

```sh
cd agent && set -a && . ./.env && set +a          # loads the three values into the shell
gh secret set LIVEKIT_URL --body "$LIVEKIT_URL"
gh secret set LIVEKIT_API_KEY --body "$LIVEKIT_API_KEY"
gh secret set LIVEKIT_API_SECRET --body "$LIVEKIT_API_SECRET"
```

The nightly is metered: the job has `timeout-minutes: 20`, the harness's own spend cap applies (`EVAL_MAX_LLM_CALLS`, `EVAL_SCENARIO_TIMEOUT_S` — see **Evals** below, overridable as repository *variables*), and a scenario that fails *twice in a row* fails the job, because a prompt regression showing up as a red nightly run is the whole point.

*What a nightly colour means.* **Red** = at least one scenario missed twice in a row, on two independent rolls with a fresh session and a fresh database each. That is the regression; read the failure. **Green** = every scenario passed, but not necessarily first time: the run's terminal summary ends with a `retried once` block naming any scenario that missed its first roll, and a scenario that keeps appearing there across nights is drifting even though the job is green. Read the block, not just the colour. `EVAL_RETRIES` (repository variable, empty = 1) is the knob; `0` restores the old one-miss-is-red behaviour.

## Testing

Two kinds, and only one of them costs money.

**Unit** — offline, no keys, no LLM. `cd backend && uv run pytest` (63 tests over the endpoints, the
audit log, call/case linking, code lookup, notes-as-events, the `since` cursor, and the migration and
foreign keys themselves, and the refusal to boot on a pre-migration database; set `DATABASE_URL` to
run the identical suite against Postgres) and `cd agent && uv run pytest` (21 tests over the
agent's pure helpers — phone validation, code normalization, the filed/second-case gates, summary
assembly, and the eval harness's own retry loop against a stand-in attempt). These are what CI
runs.

**Dashboard** — no test runner; `cd dashboard && npm run lint && npm run build` is the check CI
runs. The staff-login flow was verified by hand against a dev server: see the commands and output
in the pull request that added it.

**Evals** — `cd agent && uv run pytest -m eval` runs 21 hand-labelled scenarios through the real
`Assistant` and the real backend in-process, and checks which tools it called with what, what the
caller would hear, and what landed in the database. They make live LLM calls and need `LIVEKIT_*`
in `agent/.env`, so they are deselected by default. Results across six runs are recorded in
[agent/evals/RESULTS.md](agent/evals/RESULTS.md): **12/15** on the first prompt, **14/15** after
fixing the two misses it found, **19/19** after adding scenarios for warm transfer, `end_call`, and
null-until-classified, **20/21** on the first run that included the two multi-case scenarios, and
**21/21** on the two runs since the prompt fix that closed it. Single runs, not re-rolled.

*The cap.* A run is metered so a looping agent or a hung call cannot spend without a ceiling:
`EVAL_MAX_LLM_CALLS` is the number of LLM completions the whole pytest session may make (default
10 × the scenarios collected — a real run spends 5.4 each, 10 at the worst), and
`EVAL_SCENARIO_TIMEOUT_S` is the wall clock one scenario gets (default 90; a scenario takes ~5 s).
Over either, the run fails: the completion that would have gone over is never made, every
remaining scenario fails without calling out, and `pytest` exits non-zero. Locally that means the
score is still what the run reports — nothing forces 21/21 — while in CI a scenario that misses
twice is a red job.

*The retry.* This model misses a given scenario about 1 run in 20, so across 21 scenarios most
nights lost a different one and a red nightly stopped carrying any signal. A failed scenario is
therefore run again — a fresh `AgentSession`, a fresh database, the whole scenario including its
seeding and its DB assertions — and only the second failure raises. One miss is flake: reported,
not counted. Two in a row is the regression. Both attempts are charged to `EVAL_MAX_LLM_CALLS`, so
a flaky night costs a few completions more (a clean run spends ~116 of 210). `EVAL_RETRIES` sets
the retries per scenario — default 1, `0` restores the strict single roll. Every retry taken is
printed in a `retried once` block at the end of the run, naming the scenario and what it missed
the first time, so drift stays visible in a green log.

## API

FastAPI, [`backend/app/main.py`](backend/app/main.py). The full contract, including the shape of
every payload, is in [docs/CONTRACT.md](docs/CONTRACT.md).

| Method | Path | Notes |
|---|---|---|
| `POST` | `/cases` | body `{name, phone, issue_type?, description}`; `issue_type` is null until classified. The only response that carries `lookup_code` |
| `GET` | `/cases[?phone=&since=]` | newest first; `phone` matches digits only; `since` returns rows updated at or after an ISO timestamp (422 if it is not one) |
| `GET` / `PATCH` | `/cases/{id}` | PATCH any of `status`, `issue_type`, `description`; header `X-Source` (`staff` default, agent sends `voice`). `notes` is refused with a 422 — it moved to the row below |
| `POST` | `/cases/{id}/notes` | body `{text}` → 201 the `note` event; appends one note, `X-Source` as elsewhere |
| `GET` | `/cases/lookup?code=` | three spoken words (`blue river maple`, `Blue and River dash Maple`) → the case; 404 `no case for that code` for unknown *and* malformed; 5th wrong code on one `X-Call-Id` → 429 |
| `GET` | `/cases/{id}/events` | audit log, oldest first |
| `GET` | `/cases/{id}/calls` | that case's calls with transcripts |
| `POST` / `GET` | `/calls[?status=&room=&since=]` | a call record per voice session; `since` as on `/cases`, against the call's `updated_at` |
| `GET` / `PATCH` | `/calls/{id}` | PATCH `status` (`active` \| `needs_person` \| `ended`), `case_id`, `summary`, `transfer_reason`; a call reads back `case_id` (the case being worked now) and `case_ids` (every case it touched, in link order) |
| `POST` | `/calls/{id}/cases` | body `{case_id, how}` (`created` \| `looked_up`); links the case and makes it the current one. 201 new link, 200 already linked, 404 unknown call or case. One `call_linked` event per (case, call) |
| `POST` | `/calls/{id}/transcript` | `{role: user\|agent, text}` |
| `GET` | `/stats[?since=&until=]` | containment over a window of `started_at` (both bounds inclusive, 422 if not ISO): `{calls, contained, needs_person, active, containment}`; `containment` is null, not 0, when nothing in the window has been handled |
| `GET` | `/token?identity=` | LiveKit join token + a fresh room name for the browser call |
| `GET` | `/health` | `{"ok": true}`; the dashboard uses it to show "Backend unreachable" |
| `WS` | `/ws` | one `{type, id}` frame after every write; clients refetch |

Agent tools ([`agent/src/agent.py`](agent/src/agent.py)): `create_case(name, phone)` ·
`update_case(issue_type?, description?)` · `lookup_case(code)` · `add_note(note)` ·
`transfer_to_staff(reason)` · `end_call()`.

## Roadmap

[docs/ROADMAP.md](docs/ROADMAP.md) — five phases, each item with a "done when", plus the working
agreement the repo runs on.

## Decisions

[docs/DECISIONS.md](docs/DECISIONS.md) — one line per decision worth defending: what, why, and what
it cost. [docs/CONTRACT.md](docs/CONTRACT.md) is the API contract every feature is agreed against
before it is built.

## Known limitations

- Lookup codes are stored in plaintext, so anyone with database or dashboard access can read one. A
  caller who has lost their code has no self-service path: staff verify them another way and read it
  out of the database.
- Cases whose `lookup_code` is NULL — rows written before the column existed — cannot be reached by
  voice at all.
- The wrong-code counter is a module-level dict keyed by call id: single process, cleared on
  restart, and a caller who guesses wrong five times can still get in with the right code
  afterwards. It slows guessing on one call; it is not a real rate limiter.
- A second case is refused until the current one has both an issue type and a description, so a
  caller who raises two problems must finish describing the first. The agent's read-back of two IDs
  and two codes is only as reliable as the LLM.
- Foreign keys are public-ID strings (`calls.case_id` = `"C-1001"`) and `PRAGMA foreign_keys` is
  off; integrity is app-enforced by 404-before-write in `main.py`.
- `calls.case_id` moves to whichever case the agent is working; the full list lives in `call_cases`,
  and a client that only reads the cursor sees the last one.
- `case_events` only covers case writes: a call's status or summary changing leaves no audit row.
- The issue-type enum is written out in three places (backend `Literal`, agent prompt, dashboard
  array) — deliberate under "hardcode it twice"; the third copy is the signal to centralise it.
- `db.connect()` connections are released by CPython refcounting, not closed explicitly. Fine for
  one process.
- The home page does one `GET /calls/{id}` per active call on every refresh (N+1).
- A call whose worker dies mid-call stays `active` forever — there is no heartbeat or timeout;
  `reset_demo` is the only fix.
- The `/call` page finds its call by the room name it just generated, so a console-mode call only
  shows up under `/calls`.

## Credits

Scaffolding: `agent/` from LiveKit's
[`agent-starter-python`](https://github.com/livekit-examples/agent-starter-python), `dashboard/`
from `create-next-app`, `backend/` from a personal FastAPI + uv project template (layout and
tooling only).
