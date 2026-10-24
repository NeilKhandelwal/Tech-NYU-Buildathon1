# API contract (fixed 13:00 — all three lanes build against this)

Backend on http://localhost:8000. JSON everywhere. Errors: 404 {"detail": "..."}, 422 on bad body.

## Case
```json
{
  "id": "C-1001",              // string, "C-" + 4-digit autoincrement starting 1001; agent reads it aloud
  "name": "Maria Lopez",
  "phone": "5551234567",       // digits only, stored as given after stripping non-digits
  "issue_type": "missed_pickup", // one of: missed_pickup | pothole | streetlight | water | animal | other
                               // null until classified — the agent opens the case before it knows the type
  "description": "Trash not collected on Elm St Tuesday",
  "status": "open",            // open | in_progress | resolved
  "notes": "",                 // DERIVED, not a column: the case's note events joined by "\n",
                               // oldest first (see "## Data layer")
  "created_at": "2026-08-28T20:01:02Z",
  "updated_at": "2026-08-28T20:01:02Z"
}
```

## Endpoints
- `POST /cases` body {name, phone, description, optional issue_type} -> 201 Case (status=open, notes="", issue_type=null if omitted)
- `GET /cases` -> [Case], newest first. Optional `?phone=` (digits) filters exact match.
  Optional `?since=<ISO>` (see "## Data layer").
- `GET /cases/{id}` -> Case | 404
- `PATCH /cases/{id}` body any of {status, issue_type, description} -> Case | 404
  - `notes` is NOT accepted: 422, naming `POST /cases/{id}/notes` (see "## Data layer").
- `GET /health` -> {"ok": true}

## Stretch (locked 13:12) — a call is not a case
```json
Call: {"id": "CALL-7", "case_id": null | "C-1001", "status": "active"|"ended",
       "started_at": "...Z", "ended_at": null | "...Z", "updated_at": "...Z"}
TranscriptLine: {"id": 12, "call_id": "CALL-7", "role": "user"|"agent", "text": "...", "ts": "...Z"}
```
- `POST /calls` body {} -> 201 Call (status=active). id = "CALL-" + rowid (no offset).
- `GET /calls` -> [Call] newest first; `?status=active` filters; `?since=<ISO>` (see "## Data layer").
- `GET /calls/{id}` -> Call + `"transcript": [TranscriptLine]` | 404
- `PATCH /calls/{id}` body any of {status, case_id} -> Call | 404. status=ended sets ended_at.
- `POST /calls/{id}/transcript` body {role, text} -> 201 TranscriptLine | 404
- `GET /cases/{id}/calls` -> [Call + transcript] for that case, oldest first
- `WS /ws` -> after EVERY write (case create/patch, call create/patch, transcript append) server pushes
  one JSON text frame: {"type": "case"|"call"|"transcript", "id": "<case or call id>"}.
  Clients treat any frame as "refetch what you're showing". Never carry payloads on the socket.
- Dashboard: one socket per page, shared by every component on it; the 2s poll runs only while
  that socket is down, and every open refetches once. On any WS frame, refetch immediately.
  Home page gets a "Live calls" strip (active calls, last transcript line, link to /calls/[id]).
  /calls/[id] shows the transcript streaming; case detail lists its calls + transcripts.
- Agent: POST /calls on session start; final user utterances and agent replies -> POST transcript
  (strip `<expr .../>` tags from agent text); create_case/lookup_case PATCH call.case_id;
  on session close PATCH status=ended.

## Audit (added 13:17)
Every case write is logged; the dashboard shows it on case detail.
```json
CaseEvent: {"id": 3, "case_id": "C-1001", "field": "status", "old_value": "open", "new_value": "in_progress",
            "source": "staff", "actor": "neil", "ts": "...Z"}
```
- `GET /cases/{id}/events` -> [CaseEvent] oldest first | 404
- Written by: POST /cases (field="created", new_value=case id); PATCH /cases/{id} (one per field that
  actually changed; unchanged fields skipped); PATCH /calls/{id} with case_id (field="call_linked", new_value=call id).
- Who: `source` says which **system**, `actor` says which **person**. They are separate fields and
  both are recorded on every event, by the one helper that writes `case_events`.
  - `source` = request header `X-Source` (default "staff"). Agent sends `X-Source: voice` on every write.
  - `actor` = request header `X-Actor`, the signed-in staff name the dashboard stamps on every
    request. Stripped and capped at 64 chars; absent, empty or blank -> `null`. Voice calls have no
    actor, and rows written before the column existed have none either — `null` renders as nothing.
  - The API does not authenticate `X-Actor` yet: it records what it is told. The dashboard's
    Auth.js session is the only thing behind the name until backend auth lands.

## Agent tools (LiveKit function tools, call backend via httpx)
- create_case(name, phone) -> "Started case C-1001" (opens with issue_type="other", description=""; refuses non-10-digit phones)
- update_case(issue_type?, description?) -> fills fields on the case this call opened, as the caller explains
- lookup_case(phone) -> summary of most recent case for that phone, or "none found"
- add_note(case_id, note) -> appends line to notes
- transfer_to_staff(reason) -> PATCH call status=needs_person + transfer_reason; line stays open (see "## Warm transfer")
- end_call() -> after goodbye has played, ends the session (shutdown callback writes summary + status=ended)

## Ports
backend :8000, dashboard :3000, agent = console process (no port)

## Browser call (added 13:28) — demo from the dashboard, not the terminal
Agent runs in `dev` mode (`uv run python src/agent.py dev`, no `agent_name` → auto-dispatched to every new room).
Console mode keeps working for local testing.
- `GET /token?identity=<string>` -> {"token": "<jwt>", "url": LIVEKIT_URL, "room": "call-<8 hex>"}. Backend signs with
  LIVEKIT_API_KEY/SECRET from backend/.env (dep: livekit-api). Room name is generated per token; grants: roomJoin + publish/subscribe (the browser must publish mic audio).
- `calls` gains nullable `room TEXT`. `POST /calls` accepts optional {"room": "..."}; the agent sends ctx.room.name.
  `GET /calls?room=<name>` -> [Call] (exact match). Call JSON includes "room".
- Dashboard `/call`: "Start call" -> fetch token -> LiveKitRoom (audio only) with RoomAudioRenderer, a voice-assistant
  bar visualizer, "Hang up" (disconnect). Beside it: the live transcript of the call whose room matches, found via
  `GET /calls?room=` (poll + WS refresh like everywhere else), with a link to the case once linked.
  Deps: @livekit/components-react, @livekit/components-styles, livekit-client.
- Call end: when the browser participant disconnects, the agent job shuts down -> existing end_call marks status=ended.

## Summary (added 13:37)
- `calls` gains nullable `summary TEXT`; Call JSON includes `"summary"` (null until written).
- `PATCH /calls/{id}` accepts `{"summary": "..."}` like its other fields; staff can also edit it.
- Written by: the agent, in its shutdown callback, right before it PATCHes status=ended — it asks the
  same LLM for at most two sentences over the call's own transcript. Fewer than 2 turns, or any LLM
  failure -> no summary written (stays null); the call is still marked ended.
- Dashboard: shows it on /calls/[id] and on the case's call list; absent = "no summary".

## Warm transfer (added 14:05) — the caller asks for a person
- `CallStatus` gains `"needs_person"`: `active | needs_person | ended`. It is a live status, not a terminal
  one — `ended_at` stays null until the call is actually ended.
- `calls` gains nullable `transfer_reason TEXT`; Call JSON includes `"transfer_reason"` (null unless transferred).
- `PATCH /calls/{id}` accepts `{"status": "needs_person", "transfer_reason": "..."}` like its other fields;
  staff PATCH `{"status": "active"}` when they pick up. `GET /calls?status=needs_person` lists the queue.
- Agent tool `transfer_to_staff(reason)` -> PATCHes the call and tells the agent to keep the line open.
  Used when the caller asks for a human, is upset, or wants something outside the other tools.
- Dashboard: the home "Live calls" strip shows needs_person calls first with an amber "Needs a person" banner
  (plus the reason); /calls and /calls/[id] show an amber badge. The call stays needs_person until hang-up;
  staff actually joining the room (staff token + agent hand-off) is not built.

## Auto hang-up (added 14:20) — the agent drops the line after goodbye
- Agent tool `end_call()` -> waits for the goodbye to finish playing, 2.5 s grace, then deletes the LiveKit
  room (`DeleteRoomRequest`). No backend write: the browser disconnect drives the normal shutdown path
  (summary, then PATCH status=ended), so the call is marked ended exactly once. Never used after a transfer.

## Data layer (added 2026-08-30) — migrated, foreign-keyed, Postgres or SQLite
The endpoints and every payload above are unchanged except where this section says otherwise.

- `DATABASE_URL` picks the database. Unset -> `sqlite:///backend/cases.db`, so the local
  three-terminal path still needs no infra. Compose and CI use
  `postgresql+psycopg://…`. `CASES_DB=<path>` still works as a SQLite-only alias and logs a
  deprecation line. Alembic owns the schema: `alembic upgrade head` runs at app startup and in
  the Docker entrypoint, and nothing else creates or alters a table.
- **Ids.** Rows store integers with real foreign keys; `C-{1000+id}` and `CALL-{id}` are derived
  in the API layer. Every endpoint takes and returns the public form exactly as before, and a
  malformed public id is still a 404, never a 500. The database now rejects a call linked to a
  missing case; the API still checks first, so callers get the same 404 and the same wording.
- **Notes are events.** The `notes` column is gone. A note is a `case_events` row with
  `field="note"`, `new_value=<text>`, and its own `source` and `ts`.
  - `POST /cases/{id}/notes` body `{text}` -> 201 CaseEvent | 404. `source` from `X-Source` as
    everywhere else. Broadcasts `{"type": "case", "id": ...}` and bumps the case's `updated_at`.
  - `Case.notes` stays in the JSON, derived: those rows joined by `"\n"`, oldest first.
  - `PATCH /cases/{id}` with `notes` -> 422. Ignoring it would swallow a staff edit.
- **`since` cursor.** `GET /cases?since=<ISO>` and `GET /calls?since=<ISO>` return rows written
  **at or after** the cursor — cases by `updated_at`, calls by `updated_at`, which is new on calls
  and bumped by every write to the call, transcript lines included. Omitted still means everything.
  Timestamps are the same `...Z` strings, compared as strings.
  - Inclusive, deliberately: they are second-resolution, so a strict comparison would drop every
    row written in the cursor's own second and never return it. Clients may see one row twice,
    which costs nothing — they already refetch on every WS frame.
  - A `since` that is not an ISO-8601 timestamp is a **422**. Matching nothing would be
    indistinguishable from "nothing has changed".
- **Startup.** A database written before these migrations (the old tables, no `alembic_version`)
  is refused at startup with a message saying to move it aside; there is no in-place upgrade, and
  a half-applied one would be worse. An empty or already-migrated database starts normally.
- **`city_id`** is on `cases`, `calls` and `case_events`, defaulted to the one seeded city
  (`cities` row 1, "Demo City"). It stays internal: not in any request or response body.
- **City scope (added 2026-09-07).** One deployment serves one city, named by the `CITY_ID`
  environment variable (default `1`, the seeded city). Every read of a case, a call or a case
  event is filtered to that city and every write records it, so a case filed by one city is a
  404 from another — including by lookup code. No endpoint takes a city: it is a property of the
  deployment, not of the request, and no payload changes.

## Containment (added 2026-09-07) — `GET /stats`
The headline metric: the share of calls the agent handled with nobody from the city on the line.
```json
{"calls": 9, "contained": 5, "needs_person": 2, "active": 2, "containment": 0.7142857142857143}
```
- `GET /stats?since=<ISO>&until=<ISO>` -> Stats. Both bounds optional and **inclusive**, both on
  `calls.started_at` — when the call came in, not when it was last touched, so a window's answer
  stops changing once the window is over. `since` is inclusive for the same reason the `?since=`
  cursor is (second-resolution timestamps), and `until` matches it. A bound that is not an
  ISO-8601 timestamp is a **422**, as on the list endpoints.
- Every call in the window falls in exactly one bucket, so `calls == contained + needs_person + active`:
  - **contained** — `status="ended"` and `transfer_reason IS NULL`: it ended, and nobody ever asked
    for a person. `transfer_reason` is the marker that survives, because `transfer_to_staff` writes
    it alongside `status="needs_person"` and the end-of-call `PATCH {"status": "ended"}` leaves it
    alone. Status alone would not work: `needs_person` is overwritten by `ended`.
  - **needs_person** — `status="needs_person"` (still waiting) or ended with a `transfer_reason`.
  - **active** — `status="active"`. Excluded from the denominator: a call still on the line has not
    been contained or transferred yet. A transferred call that staff PATCH back to `active` counts
    as active while it is live, and lands in `needs_person` once it ends, because its
    `transfer_reason` is still there.
- `containment` = `contained / (contained + needs_person)`, a float in 0–1, and **null** when that
  denominator is 0. Null, not 0 and not 1: an empty window is "no calls to judge", and rendering
  either number would read as a real score. The dashboard shows `—`.
- Dashboard: a containment tile on the home page with the percentage, `contained / handled`
  underneath, and a Today / 7 days / 30 days / All window selector (default 7 days). It refetches
  through the same `useLiveRefresh` as everything else; no new socket, no second poll.
