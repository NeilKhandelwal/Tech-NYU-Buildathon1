# Roadmap

Where the project is going, in the order it makes sense to build. Each item has a
"done when" so it can be checked off without an argument. Phases are ordered by
dependency, not by ambition: nothing in Phase 4 is reachable without accounts.

## Phase 1 — Foundation

- [x] **Repo reframe** — `docs/` for the contract and decision log, a phased
      roadmap, a product README, a project `CLAUDE.md`.
      *Done when:* every relative link in `README.md`, `CLAUDE.md`, and `docs/` resolves,
      and no document describes the repo as a take-home.
      Links checked with a shell loop over the three; the only "take-home" left is the
      decision log saying where it started, which is history, not framing.
- [ ] **Docker Compose for local dev** — backend, dashboard, and agent worker from one
      `docker compose up`.
      *Done when:* a clean clone with `.env` filled in serves the dashboard and answers a
      browser call without the three-terminal dance.
      Compose runs postgres + backend + dashboard, and the `voice` profile builds the
      agent; the browser call has never been made through it, so the second half of the
      "done when" is unproven rather than done.
- [ ] **GitHub Actions CI** — `pytest` for `backend/` and `agent/` (unit only, evals
      deselected), `npm run build` and `npm run lint` for `dashboard/`.
      *Done when:* every PR shows three required checks and a red one blocks merge.
      `ci.yml` runs four jobs on every PR (backend on SQLite and on Postgres, agent,
      dashboard), but `main` has no branch protection, so none of them is *required* and
      a red one blocks nothing.
- [x] **Env-driven config** — `NEXT_PUBLIC_API_URL` for the dashboard, CORS origins from
      the backend's environment; no hardcoded `localhost:8000` or `localhost:3000`.
      *Done when:* the dashboard talks to a non-localhost backend with no code change.
      Both are env-read with a localhost default; verified by building the dashboard
      against a backend on another port and watching it talk only to that one.
- [x] **Poll as fallback only** — move the 2 s interval into `useLiveRefresh` so it runs
      only while the socket is down, and refetch once on every socket open.
      *Done when:* an idle dashboard with a healthy socket issues no periodic requests,
      and a reconnect after a disconnect catches up in one fetch.
      The interval now exists only while the socket is down, and the page's hook and the
      Nav dot share one connection instead of opening one each.

## Phase 2 — Data

- [x] **Postgres + Alembic** — replace SQLite and the `ALTER TABLE` calls in
      `db.init_db()` with real migrations.
      *Done when:* schema changes ship as reviewable migrations and no startup path
      mutates the schema.
      SQLAlchemy Core + Alembic; `DATABASE_URL` picks the engine, Postgres in compose and
      CI, SQLite by default so a local clone still needs no infra.
- [x] **`city_id` on cases, calls, and case_events** — one deployment, many cities.
      *Done when:* every read is scoped by city and a query without a city scope fails
      in tests.
      The city comes from `CITY_ID` via `db.current_city_id()` — one deployment, one city —
      and a test-suite engine listener fails any SELECT that reaches those tables without a
      `city_id` criterion, so the whole suite is the proof.
- [x] **Row-id foreign keys** — store integer keys, derive `C-1001` / `CALL-7` in the API
      layer, and turn `PRAGMA foreign_keys` (or its Postgres equivalent) on.
      *Done when:* the database rejects a call linked to a case that does not exist,
      instead of `main.py` doing it.
- [x] **Notes as `case_events` rows** — drop the `notes` column; a note is an event with
      its own source and timestamp.
      *Done when:* `add_note` is a single POST and the GET-then-PATCH race is gone.
      The column is gone, `POST /cases/{id}/notes` is the single write, and `PATCH` refuses
      `notes` with a 422. Both consumers are on it: `add_note` is one POST, and the
      dashboard's textarea is now an append-only list of notes with a time and a source.
- [ ] **`since` cursor on list endpoints** — `GET /cases?since=` and `GET /calls?since=`
      return only what changed.
      *Done when:* a refetch after a long disconnect costs one small response instead of
      the whole table.
      Backend done on both lists; the dashboard still refetches everything on reconnect, so
      the saving is not real yet. Follow-up PR.

## Phase 3 — Access

- [x] **Staff auth on the dashboard** — Auth.js with credentials first; OAuth once there
      are provider keys.
      *Done when:* every dashboard route redirects an unauthenticated visitor to a login
      page.
- [x] **Audit source is the authenticated user** — `X-Source: staff` becomes the actual
      staff identity; the agent keeps `voice`.
      *Done when:* a case's history names who made each change, not just which system.
      Done as a second field, not a replacement: `source` still says which system, `actor`
      says which person. The dashboard sends `X-Actor` from the session; the backend trusts
      it until backend auth lands.
- [x] **Nightly eval run in CI** — the scenario suite on a schedule, with a hard cap on
      LLM spend per run.
      *Done when:* a prompt regression shows up as a failed nightly run and the cap stops
      the job before the bill does.
      `EVAL_MAX_LLM_CALLS` completions per run (default 10 × the scenarios) and
      `EVAL_SCENARIO_TIMEOUT_S` per scenario, both enforced in the harness and passed
      through the workflow under `timeout-minutes: 20`; the three `LIVEKIT_*` repository
      secrets are the one step left, and the job skips with a notice until they are set.

## Phase 4 — Voice for real

Needs accounts (Twilio, LiveKit Cloud, Fly, Vercel); nothing here is buildable on
localhost alone.

- [ ] **Twilio SIP → LiveKit ingress** — a real phone number reaches the same agent.
      *Done when:* a call from a mobile phone creates a case, with no change to
      `agent/src/agent.py`.
- [ ] **Deploy** — backend on Fly, dashboard on Vercel, agent via `lk agent deploy`.
      *Done when:* the three pieces run from one merge to `main`, and the dashboard is a
      URL somebody else can open.
- [ ] **Real staff pickup** — a staff token and a join page so a person actually enters
      the room and the agent stands down.
      *Done when:* a `needs_person` call ends with a human on the line, not just an
      amber badge.
- [ ] **Egress recordings** — call audio stored alongside the transcript.
      *Done when:* a case's call list plays back the audio for any completed call.
- [ ] **SMS of case ID and lookup code** — texted to the caller at the end of the call.
      *Done when:* a caller who never wrote the code down can still reach their case.

## Phase 5 — Product

- [x] **Containment on the home page** — share of calls handled with no staff, over a
      chosen window.
      *Done when:* the number on the page matches a hand count of the same calls.
      `GET /stats?since=&until=` over `calls.started_at`; contained = ended with no
      `transfer_reason`, active calls excluded from the denominator, null on an empty window.
- [ ] **Coverage report** — transfers and failed lookups grouped by reason, ranked by how
      many residents each one cost.
      *Done when:* a week of calls produces a ranked list of fixes, not a log.
- [ ] **Approved-KB answers** — routine questions (hours, fees, where to pay) answered
      from an approved knowledge base, with a "couldn't answer" event when it has no entry.
      *Done when:* the coverage report is fed by real "couldn't answer" rows rather than
      transfer reasons.
- [ ] **Department routing** — a case reaches the right department, and the call can be
      routed to that department's agent.
      *Done when:* an issue type maps to a department and a misroute is visible as an
      event.
- [ ] **Spanish** — STT, TTS, and prompt follow the caller's language; tools keep writing
      English into the case.
      *Done when:* a Spanish call files a case a monolingual English staff member can read.

## Working agreement

- **Contract first.** Every feature starts with an addition to
  [CONTRACT.md](CONTRACT.md), agreed before any of the three sides is built against it.
- **Branch and PR.** No commits straight to `main`; feature work happens in a git
  worktree so the demo checkout stays runnable.
- **Path-scoped commits.** One commit touches one concern, and its message says why.
- **An eval scenario per agent-behaviour change.** A change to the prompt or a tool ships
  with the scenario that would have caught it.
- **The decision log stays current.** [DECISIONS.md](DECISIONS.md) gets a line for every
  decision worth defending, when it is made and not afterwards.
- **No AI attribution in git.** No co-author trailers, no generated-with footers.
