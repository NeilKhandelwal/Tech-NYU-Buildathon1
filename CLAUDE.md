# CLAUDE.md — project operating context

## What this is
A voice intake line for local-government service requests, with a live case desk.
A LiveKit agent answers the phone, files a service request mid-conversation, and hands
the caller a case ID and a three-word lookup code; staff watch the call and triage the
case in a Next.js dashboard, live. Read [README.md](README.md) first — it has the data
model and the API table.

## Layout
- `backend/` — FastAPI + SQLAlchemy Core (no ORM). `app/main.py` is every endpoint,
  `app/db.py` the tables, the engine and the public-id helpers, `migrations/` the Alembic
  schema, `app/models.py` the Pydantic types, `app/codes.py` + `app/words.py` the lookup
  codes. `scripts/seed.py`, `scripts/reset_demo.py`.
- `agent/` — LiveKit Agents worker. `src/agent.py` is the prompt, the six function tools,
  and the session wiring. `tests/` has unit tests plus `test_scenarios.py` (evals).
- `dashboard/` — Next.js App Router. `src/lib/api.ts` is the whole client (types, fetch
  helpers, `useLiveRefresh`); pages under `src/app/`.
- `docs/` — [CONTRACT.md](docs/CONTRACT.md), [DECISIONS.md](docs/DECISIONS.md), [ROADMAP.md](docs/ROADMAP.md).

## Run and test
- Backend: `cd backend && uv sync && uv run uvicorn app.main:app --reload --port 8000`;
  tests `uv run pytest`. `DATABASE_URL` picks the database — unset means the SQLite file
  `backend/cases.db`, `postgresql+psycopg://…` is what compose and CI run. Migrations apply
  themselves at startup; `uv run alembic upgrade head` does it by hand.
- Agent: `cd agent && uv sync && uv run python src/agent.py dev` (or `console`);
  tests `uv run pytest`. Evals: `uv run pytest -m eval` — live LLM calls, needs
  `LIVEKIT_*` in `agent/.env`, deselected by default. Never let CI run them unmetered.
- Dashboard: `cd dashboard && npm install && npm run dev`; checks `npm run build` and
  `npm run lint`.

## Conventions
- **Stdlib first.** No new dependency without a reason stated in the PR description.
- **Short, obvious functions** over clever ones. Every non-obvious line gets a one-line
  comment saying *why*, not what.
- **Contract first.** A feature starts by adding to `docs/CONTRACT.md`, agreed before the
  backend, agent, and dashboard are built against it. That contract is what lets the three
  sides be written in parallel.
- **Hardcode twice, abstract on the third.** Two copies of a value or a shape are fine and
  deliberate; the third copy is the signal to centralise. Say so in a comment when you do
  it on purpose (the `issue_type` enum is the standing example, at three copies).
- **Match what is there.** Conformance beats taste inside this codebase. If a convention
  seems wrong, say so once, in the PR — do not fork it silently.
- **Errors the agent can speak.** Every agent tool swallows backend failures and returns a
  sentence the caller can hear. A tool must never raise into the session.
- **Writes broadcast.** Any new write endpoint ends with `await broadcast(type, id)`;
  the socket carries no payload and clients refetch.
- **No AI attribution in git.** No co-author trailers, no generated-with footers.

## Working in this repo
- **The main checkout is the demo tree.** Services run from it; never run git operations
  or merges there. Feature work happens in a git worktree:
  `git worktree add ../wt-<name> -b <branch> origin/main`.
- Branch and PR for everything; no direct commits to `main`.
- Path-scoped commits: one commit, one concern, a message that says why.
- An agent-behaviour change (prompt or tool) ships with the eval scenario that would have
  caught the old behaviour.

## Where decisions go
`docs/DECISIONS.md`, one line per decision worth defending, written when the decision is
made: `YYYY-MM-DD | decision | why | what we gave up`. Take-home-era entries use elapsed
`HH:MM` and stay as they are. If a decision is worth arguing about in review, it belongs
in that file before the PR is opened.
