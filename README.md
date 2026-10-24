# CityLine (placeholder name) — an ElevenLabs voice agent that files cases while you talk

A voice intake line for local-government service requests, with a live case desk.

## What it is

A resident calls the City services line. An ElevenLabs voice agent answers, takes their name and
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
 mic ──► ElevenLabs Agents (STT · LLM · TTS · turn-taking)        dashboard/ (Next.js) ◄── Chrome
   ▲  WebRTC         │ server tools: POST /voice/tools/*                ▲  fetch + WS /ws
   │                 │ post-call webhook: POST /voice/webhooks/post-call │
 /call page ─────────┼──► backend/ (FastAPI + SQLAlchemy Core) ── broadcast {type,id} ┘
 (@elevenlabs/react) │       tables: cases · calls · call_cases · transcript · case_events
   relays transcript ┘
```

Two processes we run, one we configure. The voice agent lives in ElevenLabs; its prompt and its
five tool schemas live in [agent/provision.py](agent/provision.py), which creates or updates the
agent from code. Everything the agent does to a case is an HTTP call into the backend
([docs/CONTRACT.md](docs/CONTRACT.md) "## ElevenLabs voice").

**Real-time.** After every write the backend pushes `{"type": "case"|"call"|"transcript", "id": ...}`
on `WS /ws`. The socket carries no payload; clients refetch what they are showing. Refetch is
idempotent, so duplicate or out-of-order frames cannot corrupt UI state. There is one socket per
page, shared by everything on it. The 2-second poll is a fallback that exists only while that
socket is down.

**Voice.** ElevenLabs Agents runs the whole conversation: Scribe speech-to-text, the LLM, ElevenLabs
TTS, interruption handling and turn-taking. The browser joins over WebRTC with a short-lived
conversation token that the backend mints (`POST /voice/session`), so the API key never leaves the
server. The session carries one dynamic variable, `call_id`, and every server tool reads it from
there, never from the LLM, so the model cannot write to someone else's call.

The five tools (`create_case`, `update_case`, `lookup_case`, `add_note`, `transfer_to_staff`) are
**server tools**: webhooks into `/voice/tools/*`, authenticated with a shared `X-Voice-Secret`.
Hang-up is ElevenLabs' built-in `end_call`. A webhook is stateless, so the per-call state the old
in-process agent held — which case is being worked, whether it is filed, whether its lookup code
may be read out — is rebuilt from the database on every request. Every tool answers 200 with a
sentence; a failure is something the agent can say, never a silent 4xx.

The `/call` page relays the SDK's live transcript to the backend line by line, so staff watch the
call as it happens. When the call ends, ElevenLabs' post-call webhook (HMAC-verified) writes its own
call summary onto the call, backfills the transcript if nothing was streamed, and closes the call.

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
junk case. A call carries `status` (`active | needs_person | ended`), `room` (unused since the move off
LiveKit, kept so old rows still read), `summary` (ElevenLabs' post-call summary), and
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

**Prereqs:** [uv](https://docs.astral.sh/uv/) (installs Python 3.13 itself), Node 20.9+, an
ElevenLabs account, and a tunnel so ElevenLabs can reach your local backend
(`brew install cloudflared`). Without ElevenLabs everything except the voice call works.

```sh
# 0. a shared secret for the tool webhooks
python3 -c "import secrets; print(secrets.token_urlsafe(24))"

# 1. backend (http://localhost:8000, SQLite file backend/cases.db; override with DATABASE_URL=<url>)
cd backend && cp .env.example .env    # ELEVENLABS_API_KEY, VOICE_TOOL_SECRET; agent id after step 3
uv sync && uv run python -m scripts.seed && uv run uvicorn app.main:app --reload --port 8000

# 2. public URL for the backend (prints https://<random>.trycloudflare.com)
cloudflared tunnel --url http://localhost:8000

# 3. create the ElevenLabs agent and its tools, pointed at that URL
cd agent && cp .env.example .env      # ELEVENLABS_API_KEY, PUBLIC_BACKEND_URL, VOICE_TOOL_SECRET
uv sync && uv run python provision.py # prints the agent id: put it in agent/.env and backend/.env,
                                      # then restart the backend. Re-run after editing the prompt,
                                      # or whenever the tunnel URL changes.

# 4. dashboard (http://localhost:3000; open it at localhost, not 127.0.0.1, for CORS)
cd dashboard && npm install && npm run dev
```

**Post-call webhook** (summaries, and the backstop that ends a call): in the ElevenLabs dashboard,
go to Agents → Settings → Webhooks, add `https://<tunnel>/voice/webhooks/post-call` with the
**Transcript** event, then copy its HMAC secret into `ELEVENLABS_WEBHOOK_SECRET` in
`backend/.env`. The call works without it. You just won't get the ElevenLabs summary.

**SMS (Twilio)**, optional: without it every text is still recorded with status `not_sent`
(`GET /sms`), which is what the dashboard's SMS panel shows.
1. In the Twilio console, copy **Account SID** and **Auth Token** into `TWILIO_ACCOUNT_SID` /
   `TWILIO_AUTH_TOKEN` in `backend/.env`, and buy or pick an SMS-capable number for
   `TWILIO_PHONE_NUMBER` (`+1…`).
2. A trial account only texts verified numbers: add each teammate's phone under
   Phone Numbers → Verified Caller IDs. An unverified number comes back as `failed`, error 21608.
3. Inbound texts: set `PUBLIC_BACKEND_URL` to the tunnel URL, then on the number's page set
   **A message comes in** → Webhook, `POST`, `https://<tunnel>/sms/webhooks/twilio`. The signature
   is checked against exactly that URL, so update both when the tunnel changes.
4. Restart the backend and send one: `curl -X POST localhost:8000/missions/M-101/sms
   -H 'content-type: application/json' -d '{"text_en": "Test from Heat Check"}'`.

- Env vars: `NEXT_PUBLIC_API_URL` (dashboard → backend, default `http://localhost:8000`), `CORS_ORIGINS` (backend, comma-separated, default `http://localhost:3000`), `DATABASE_URL` (backend; unset means the SQLite file), the `ELEVENLABS_*` / `VOICE_TOOL_SECRET` set above, and `TWILIO_*` / `PUBLIC_BACKEND_URL` for SMS. All of them are listed in the matching `.env.example`.
- Staff login: set `AUTH_SECRET` (`openssl rand -base64 32`) and `STAFF_USERS="name:hash,..."` in `dashboard/.env.local`, where each hash comes from `cd dashboard && npm run hash-password -- <password>`. **Dev-only shortcut:** leave `STAFF_USERS` unset and dashboard auth is disabled entirely. Never deploy with it unset.

Then open http://localhost:3000/call and press **Start call** (Chrome asks for the mic once).

Reset to a clean state at any time: `cd backend && uv run python -m scripts.reset_demo` — wipes all
tables and reseeds the 3 sample cases, printing each case's lookup code.

### Run with docker

```sh
docker compose up --build            # postgres + backend :8000 (migrated and seeded) + dashboard :3000
docker compose down                  # stop; add -v to also drop the postgres volume
```

The voice agent needs no container: it runs in ElevenLabs. Point the tunnel at `localhost:8000`
as above, and pass the `ELEVENLABS_*` / `VOICE_TOOL_SECRET` values through `backend/.env`.

### CI

`.github/workflows/ci.yml` runs on every push to `main` and every PR: backend pytest on SQLite and
against `postgres:16` (the same suite both times), agent pytest, and dashboard lint + build.

## Testing

Everything runs offline, with no keys.

- `cd backend && uv run pytest` covers the endpoints, the audit log, call/case linking, code lookup,
  notes-as-events, the `since` cursor, migrations, and `tests/test_voice.py`. That file drives the
  server tools the way a call would: the lookup code is withheld until the case is filed, a filed
  or looked-up case can't be rewritten, a second case waits for the first, partial phone numbers
  are refused, wrong codes are rate-limited, and the post-call webhook's signature check, summary
  write and transcript backfill all hold.
- `cd agent && uv run pytest` checks the provisioned config against the backend: every tool takes
  `call_id` from the session and not the LLM, every tool has a matching route and sends the
  secret, and the `issue_type` enum matches the backend's.
- `cd dashboard && npm run lint && npm run build`.

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
| `POST` | `/voice/session` | opens a call and returns `{call_id, token}`: an ElevenLabs WebRTC conversation token for the browser |
| `POST` | `/voice/tools/{create_case,update_case,lookup_case,add_note,transfer_to_staff}` | ElevenLabs server tools; `X-Voice-Secret` required; always 200 `{result}` |
| `POST` | `/voice/webhooks/post-call` | ElevenLabs post-call webhook, HMAC-verified: summary, transcript backfill, ends the call |
| `GET` | `/health` | `{"ok": true}`; the dashboard uses it to show "Backend unreachable" |
| `WS` | `/ws` | one `{type, id}` frame after every write; clients refetch |

Agent tools ([`agent/provision.py`](agent/provision.py)): `create_case(name, phone)` ·
`update_case(issue_type?, description?)` · `lookup_case(code)` · `add_note(note)` ·
`transfer_to_staff(reason)` · built-in `end_call`.

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
- If the browser tab dies mid-call and the post-call webhook isn't configured, the call stays
  `active`; `reset_demo` is the only fix.
- `call_id` is a dynamic variable the browser sets, so a resident with devtools could point their
  session at another active call id. Tools still need the shared secret, which only ElevenLabs has;
  binding the token to the call server side is the fix.
- The tool secret sits in the agent's config in ElevenLabs as a plain header value, not a
  workspace secret.
- Phone calls (ElevenLabs + Twilio) aren't wired up yet: a call that doesn't start from `/call`
  has no `call_id`, so its tools answer with "couldn't reach the case system".

## Credits

Scaffolding: `dashboard/` from `create-next-app`, `backend/` from a personal FastAPI + uv project template (layout and
tooling only).
