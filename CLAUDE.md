# Heat Check — project context (FINAL)

Hackathon build. Tech@NYU Shipyard Buildathon, Sat Oct 24 2026.
- **Deadline: submit on MLH by 3:45 PM ET.** Team targets: feature freeze 3:00, submit 3:30.
- Main track: Best Use of ElevenLabs. Judges want voice central to the experience, not a bolt-on.
- Also enter:
  - SpaceX/Grok, only if Grok does the translation/summaries.
  - Snowflake, only if the heat map ships.
- Skip DigitalOcean.

Detailed specs live next to this file:
- `CONTRACT.md`: DB schema and every API endpoint. This is the source of truth between frontend, backend and agents.
- `AGENTS.md`: the two ElevenLabs agents, their prompts and their tools.
- `DASHBOARD.md`: the government caseworker dashboard spec.
- `PITCH.md`: the problem stats, demo script and judge Q&A.

## What we're building (one sentence)
A two-sided voice line for NYC no-heat complaints. Residents call in their own language, the case lands on a hosted government dashboard, and the caseworker's reply goes back to the resident as a phone call in their language. A human approves every step on both sides.

**Pitch line:** "311 that speaks your language, and a caseworker console that talks back."

## The pipeline
1. **Resident calls the Heat Check number.** Often an elderly or ESL resident, in their native language.
2. **Intake agent (ElevenLabs).**
   - Auto-detects the language and replies in it.
   - Collects name, address, unit, days without heat, indoor temperature, and callback number (default: caller ID).
   - Calls `check_violation`, which compares the outdoor temperature at that hour with the HPD rule.
3. **Resident human-in-the-loop.** The agent reads the details back and asks, "Should I send this to the city?" Only a yes calls `submit_case`.
4. **Confirmation text** goes to the resident in their language, with the case number.
5. **Post-call webhook.** It saves the original transcript and makes an English translation, summary and priority with an LLM (Grok, if possible). It also runs `building_lookup` from NYC Open Data. The case then appears live on the dashboard.
6. **Government dashboard (hosted Next.js).** The caseworker sees:
   - the queue
   - the case detail: original and English transcript, violation check, building history, AI-suggested action
7. **Caseworker human-in-the-loop.**
   - Picks an action: schedule inspection, need more info, emergency repair referral, or resolved.
   - Edits the message in English.
   - Nothing goes out until they click **"Call back in resident's language."**
8. **Callback agent (ElevenLabs outbound via Twilio).**
   - Calls the resident and delivers the caseworker's message in the resident's language.
   - Captures the reply (confirmed, needs another time, or a question) with `record_resident_response`.
9. **The resident's reply shows on the dashboard live.** A receipt text goes to the resident in their language.

**Cut (say "next" in the pitch):**
- the agent calling the landlord directly
- whole-building / neighbor filing
- scheduled twice-daily evidence texts
- real 311/HPD integration
- other complaint types: hot water, mold, lockouts
- voice clone
- PDF export

## The law (verified on the NYC HPD site)
- Heat season runs Oct 1 to May 31.
- Day (6am–10pm): if it's below 55°F outside, it must be at least 68°F inside.
- Night (10pm–6am): it must be at least 62°F inside, whatever the outdoor temperature.
- Hot water must be at least 120°F year-round.
- Penalties: $250–$500/day for a first violation; $500–$1,000/day for a repeat at the same building.
- Emergency Repair Program: if the owner doesn't fix it after a violation and it's below 64°F (day) or 58°F (night), HPD can hire contractors and bill the owner.

```
def is_violation(date, hour, indoor_f, outdoor_f):
    if not in_heat_season(date): return False      # Oct 1 – May 31
    if 6 <= hour < 22: return outdoor_f < 55 and indoor_f < 68
    return indoor_f < 62
```

## Architecture
- **Voice:** ElevenLabs Agents. There are two agents: `intake` (inbound) and `callback` (outbound).
  - Phone goes through ElevenLabs' native Twilio integration.
  - Tools are ElevenLabs server tools, i.e. webhooks into our API.
  - Outbound calls take dynamic variables.
  - A post-call webhook sends us transcripts.
  - Check current ElevenLabs docs for exact field names; don't guess.
- **SMS:** Twilio. A trial account only texts verified numbers, so verify all 4 teammates' phones.
- **App:** Next.js (App Router) on Vercel. API routes live under `app/api`.
- **DB:** Supabase Postgres + Realtime (the dashboard subscribes to `cases` and `events`).
- **LLM for translation/summary/priority:** Grok if keys are available (makes us eligible for the SpaceX track), else any LLM.
- **Data:**
  - NYC Open Data (Socrata): HPD violations and 311 heat/hot water service requests by address.
  - Cache the demo building's data in Supabase.
- **Weather:** Open-Meteo (no key) for the outdoor temperature at a lat/lon and hour.

## Rules (agents and code)
- Agents always say they are an AI assistant for Heat Check.
- No legal advice: "your rights may include…", and point to 311/HPD.
- Never submit a case without the resident's spoken yes. Never call a resident back without the caseworker's click.
- Everything the resident hears or reads is in their language. Everything the caseworker reads is in English, with the original shown alongside.
- Never ask for SSN, bank details or immigration status.
- The dashboard is a **demo government console** labeled "Heat Check Caseworker Console (demo)". It must not use NYC or HPD logos or branding.
- Never submit anything to the real 311/HPD. Case numbers are ours: `HC-` + 6 digits.

## Team ownership and code layout
| Person | Owns | Folder |
|---|---|---|
| Romir | Both ElevenLabs agents (prompts, language detection, tools, dynamic variables), with Neil | `/agent` |
| Neil | Twilio, Supabase schema, all `app/api` routes, LLM translation, Open Data + weather | `app/api`, `/lib`, `/supabase` |
| Andrew | Caseworker dashboard UI, realtime, Vercel deploy | `app/(dashboard)`, `/components` |
| Valaya | Pitch, stats, seed data (4–5 realistic cases + demo building), caseworker role in the demo, backup video, MLH submission | `/seed`, `/pitch` |

## Checkpoints
| Time | Done means |
|---|---|
| 12:30 | Repo, Supabase, Vercel, Twilio number, ElevenLabs keys ready; 4 phones verified |
| 1:00 | Intake agent answers in Hindi and English and runs intake; `create_case` writes a row |
| 1:30 | Case appears on the dashboard live with English translation, violation flag and building history |
| 2:15 | Caseworker click → callback call in Hindi delivers the message; resident reply saved |
| 2:30 | Confirmation and receipt texts work (or the SMS panel fallback is on) |
| 2:45 | Full demo run-through; fix blockers only |
| 3:00 | Feature freeze; record backup video |
| 3:30 | Submitted on MLH |

## Fallbacks
| If this breaks | Fall back to |
|---|---|
| Inbound number not working by 1:00 | Web mic button using the ElevenLabs React SDK on a "resident" page |
| Outbound callback fails | Callback via web session the dashboard opens in a second tab |
| Twilio SMS blocked | SMS log panel on the dashboard showing what would be sent |
| LLM translation slow or fails | Show the agent's own English summary from the post-call analysis |
| Open Data or weather API slow | Cached values in Supabase for the demo building |
| Live demo fails | Backup video |

## Working style for Claude Code
- Speed over polish. Make the smallest change that works and keep the demo path green.
- Build only what's in this file. Ask before adding anything else.
- Secrets go in `.env.local` only, never committed. Use:
  - `ELEVENLABS_API_KEY`, `ELEVENLABS_INTAKE_AGENT_ID`, `ELEVENLABS_CALLBACK_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID`
  - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`
  - `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
  - `XAI_API_KEY` (or another LLM key)
- Small commits, pushed often. Four people share this repo. Stay in your folder.
- If you change anything in `CONTRACT.md`, say so in the team chat.

## Links
- Team doc (Google Doc "Voice Agent", tab "Final - Two-Sided Heat Line"): https://docs.google.com/document/d/16DWj0tdnHWwBuJBAAtpWlFXjRKmkVLnHHNBtzWZTrNY/edit
- HPD heat rules: https://www.nyc.gov/site/hpd/services-and-information/heat-and-hot-water-information.page

---

## Repo as it is today (carried over from the previous CLAUDE.md)
Everything above says **what** we're building and wins on that. This section says **how the code on `main` is actually set up**. Where the two disagree (stack, folders, tables, routes, env names), the code below is what exists. The mismatches are listed in the PR that added this file; update `CONTRACT.md` once the team picks a side.

### Spec vs code conflicts (resolve in CONTRACT.md)
- **Stack:** the spec says Next.js `app/api` + Supabase. `main` is a FastAPI backend (`backend/`), a separate Next.js dashboard (`dashboard/`) and a LiveKit agent (`agent/`). Branches `tenant-advocate`, `lane/heat` and `elevenlabs-voice` move the agent to ElevenLabs on the same FastAPI backend.
- **Folders:** `app/api`, `/lib`, `/supabase`, `/seed` and `/pitch` don't exist yet. In the ownership table, read `app/api`/`/lib`/`/supabase` as `backend/`, `app/(dashboard)`/`/components` as `dashboard/src/`, and `/agent` as `agent/` (which already exists).
- **Tables:** `main` has `cities`, `cases`, `calls`, `call_cases`, `transcript` and `case_events`, not `cases` + `events`.
- **Secrets:** the spec says one `.env.local`. The code reads per-service `backend/.env`, `agent/.env` and `dashboard/.env.local`. Every `.env*` except `.env.example` is gitignored.

### Layout
- `backend/`: FastAPI + SQLAlchemy Core (no ORM). `app/main.py` holds every endpoint; `app/db.py` the tables, the engine and the public-id helpers (`C-{1000+id}`, `CALL-{id}`); `migrations/` the Alembic schema; `app/models.py` the Pydantic types; `app/codes.py` + `app/words.py` the three-word lookup codes. Also `scripts/seed.py` and `scripts/reset_demo.py`.
- `agent/`: LiveKit Agents worker. `src/agent.py` holds the prompt, six function tools and the session wiring. `tests/` has unit tests plus `test_scenarios.py` (evals).
- `dashboard/`: Next.js App Router. `src/lib/api.ts` is the whole client (types, fetch helpers, `useLiveRefresh`); pages live under `src/app/`.
- `docs/`: the **old** `CONTRACT.md` (service-request desk), `DECISIONS.md` and `ROADMAP.md`. The root `CONTRACT.md` is the new source of truth.

### Run and test
- Backend: `cd backend && uv sync && uv run uvicorn app.main:app --reload --port 8000`; tests `uv run pytest`. Migrations apply themselves at startup; `uv run alembic upgrade head` does it by hand.
- Agent: `cd agent && uv sync && uv run python src/agent.py dev` (or `console`); tests `uv run pytest`. Evals: `uv run pytest -m eval` makes live LLM calls and is deselected by default. Never let CI run them unmetered.
- Dashboard: `cd dashboard && npm install && npm run dev`; checks `npm run build` and `npm run lint`.
- Compose: `docker-compose.yml` runs Postgres + backend + dashboard. Ports: backend :8000, dashboard :3000.

### Env vars already in use
- backend: `DATABASE_URL` (unset = SQLite `backend/cases.db`; `postgresql+psycopg://…` in compose/CI), `CITY_ID`, `CORS_ORIGINS`, `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- agent: `LIVEKIT_*`, `BACKEND_URL`
- dashboard: `NEXT_PUBLIC_API_URL`, `AUTH_SECRET`, `STAFF_USERS`
- on `tenant-advocate` (unmerged): `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_CALLER_AGENT_ID`, `ELEVENLABS_TENANT_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID`, `ELEVENLABS_WEBHOOK_SECRET`, `VOICE_TOOL_SECRET`, `GEMINI_API_KEY`, `PUBLIC_BACKEND_URL`

### Conventions already implemented
- **Writes broadcast.** Every write endpoint ends with `await broadcast(type, id)` on `/ws`. The socket carries no payload, and clients refetch.
- **Errors the agent can speak.** Agent tools swallow backend failures and return a sentence; they never raise into the session.
- **Stdlib first; short, obvious functions;** comment the *why* of non-obvious lines; match what is there.
- **Decisions** go in `docs/DECISIONS.md`, one line each: `YYYY-MM-DD | decision | why | what we gave up`.

### Git
- The main checkout is the demo tree; never run git operations there. Work in a worktree: `git worktree add ../wt-<name> -b <branch> origin/main`.
- Branch and PR for everything, with no direct commits to `main`. Path-scoped commits: one concern each, with a message that says why.
- No AI attribution in git: no co-author trailers, no generated-with footers.
