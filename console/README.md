# Heat Check Caseworker Console (demo)

The government side of Heat Check: the caseworker queue, case detail, and the
"Call back in the resident's language" action. Spec: `DASHBOARD.md` (on `main`).
It covers **all 311 reports**, not only heat, and shows **every call while it's happening**.

- **Reads** come straight from Supabase (`cases`, `events`), live over Realtime.
- **311 types:** `cases.issue_type` is one of heat_hot_water, missed_pickup, pothole,
  streetlight, water, animal, other. Null on older rows means heat.
  - Heat cases keep the violation check and building history.
  - Every other type gets a Report card with the resident's description, plus generic
    message templates.
  - The queue filters by problem type.
- **Live calls:** `backend/app/live.py` logs each call to the agents as `call_started` /
  `transcript` / `call_ended` rows in `events` (case_id null until the agent files the case).
  - The queue pins **On the line now** at the top, and a **Calls** tab lists the last
    3 hours.
  - Each line shows the original words, with the English translation under it once it arrives.
  - A live call on a case leads that case's view. Watching a call that files its case
    switches to the case.
- **Messages** to residents go over WhatsApp in the demo; the panel shows `sms_out` /
  `sms_in` events whichever channel sent them.
- **Writes** go to the Heat Check API on Fly: `POST /api/cases/{id}/action`, then
  `POST /api/cases/{id}/callback`.
- No login: it's a demo console for a projector.

```
cp .env.example .env.local   # fill in the publishable key
npm install
npm run dev                  # http://localhost:3000
```

Vercel: import the repo with **Root Directory = `console`** and set the three
`NEXT_PUBLIC_*` variables from `.env.example`.

## Turning on all-311 and live calls (branch `hc/311`)

Order doesn't matter: the API works before the SQL runs, and the old agent keeps filing heat.

1. **Supabase SQL editor:** run the last block of `supabase/schema.sql` (two
   `alter table cases add column if not exists`). Until then `issue_type` reads as null,
   and every case shows as heat.
2. **Fly:** deploy, and set `ELEVENLABS_INTAKE_AGENT_ID` (and `ELEVENLABS_CALLBACK_AGENT_ID`)
   so the call log watches those agents. With neither set, it watches every conversation
   in the workspace. `HC_LIVE_CALLS=0` turns it off.
3. **Agents:** `cd agent && uv run python provision.py` re-provisions the intake agent (311
   prompt, `issue_type` / `description` on `create_case`) and the callback agent (generic
   greeting, `{{issue_label}}`).
4. **Live lines** need the agents' **Monitoring** toggle (Advanced settings), which ElevenLabs
   offers on Enterprise or with the realtime-monitoring flag.
   - Without it, calls still appear live, with a running timer. The relay then polls the
     conversation's transcript, which ElevenLabs may only fill in when the call ends.
   - Either way, the post-call webhook writes the full transcript to the case.

Tested on `hc/311`:

| Check | Result |
|---|---|
| Backend suite | 276 passed, including 22 new in `test_hc_311.py` and `test_live.py` |
| Agent suite | 16 passed, 2 new: issue enum matches the backend; the heat rule is heat-only |
| `groupCalls` logic (scratch script) | 8 assertions pass: live vs ended vs stale, a late translation, a late case link |
| API boot with the poller on (fake key) | `/health` 200; the poller hits ElevenLabs every 2 s and logs the 401 without crashing |
| Console lint, clean build; real page against the real Supabase URL | Pass, no hydration errors |
| Mock-data preview: live call with no case; a pothole case with a live call | Renders as intended |
| A real call through the poller, the monitoring socket, and Supabase | **Not run: no ElevenLabs or Supabase key on this machine** |

## Test results (2026-10-24, against `heatcheck` @ 53733ae)

Resident messages now go over **WhatsApp** (the Twilio sandbox, `TWILIO_CHANNEL=whatsapp`),
not iMessage or plain SMS. The events are still `sms_out` / `sms_in`, so the console needs no
data changes. The panel is now titled "Messages · WhatsApp / SMS", because the events
don't record which channel was used.

| Check | Result |
|---|---|
| `npm run lint`, clean `npm run build` | Pass |
| Console logic: 8 call-button cases, 7 rule texts, 2 address cases (scratch script) | Pass |
| Backend Heat Check tests: `test_hc_api`, `test_hc_lib`, `test_hc_webhooks`, `test_hc_reset` | Pass, 46 passed |
| `GET /api/cases` on Fly: has every column the console reads | Pass, 4 seeded cases |
| CORS preflight for the console's `POST` from another origin | Pass: `allow-origin: *`, `content-type` allowed |
| `POST /api/cases/{id}/action` with an invalid action (writes nothing) | Pass: `422` naming the four valid actions |
| `POST /api/cases/{unknown}/callback` (places no call) | Pass: `404 case not found` |
| Supabase project reachable | Pass: REST answers `401 No API key` without a key |
| Console with no Supabase env | Pass: shows "Supabase isn't configured" |
| Console with a wrong key, real project URL | Pass: shows "Could not read cases: Invalid API key", Live dot red |
| Reads and Realtime through Supabase with the real key | **Not run: no key on this machine** |
| Save action and **Call back** from the console, end to end | **Not run: it would place a real call** |

**Fixed in this round:** the backend changed underneath the console.
- `check_violation` can append " (demo: simulated cold day)" to the rule text. That hid the
  legal minimum and the temperature bar, exactly in the demo setup.
- The post-call callback event now carries `payload.stage = "ended"`. A no-pickup call with
  an empty transcript would have left the button on "Calling…" for good.

## Still missing before the demo

1. **Supabase publishable key (blocker).** Put the full
   `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` (Neil has it) in `console/.env.local` and in
   Vercel. This is the only thing between the console and the untested rows of the table
   above.
2. **RLS really off on the live project.** `supabase/schema.sql` now disables RLS on
   `cases` / `events` (b31ced9), but that only applies if someone re-ran it in the SQL
   editor. The symptom isn't an error: with RLS on, reads and Realtime return **nothing**,
   so the queue sits at "Waiting for the first case…" while Fly's `GET /api/cases` shows 4.
3. **Realtime publication.** Check that
   `alter publication supabase_realtime add table cases, events;` ran. If the header stays on
   "Connecting", or new cases appear only after a reload, it didn't.
4. **WhatsApp sandbox (owner: Romir/Neil).**
   - Fly needs `TWILIO_CHANNEL=whatsapp`.
   - Every demo phone must send the sandbox join code to +1 415 523 8886. Re-send it the
     morning of the demo, because the sandbox drops participants after a while.
   - The sandbox's "When a message comes in" URL must point at
     `https://heatcheck-nyu.fly.dev/api/webhooks/twilio_sms`, or replies never reach the
     panel. The webhook already strips the `whatsapp:` prefix.
   - A message that fails still shows in the panel as "not delivered".
5. **Callback configuration on Fly.** `/callback` needs `ELEVENLABS_CALLBACK_AGENT_ID` and
   `ELEVENLABS_PHONE_NUMBER_ID`. If either is missing, the button shows the API's 503 message.
6. **Seed data is thin for the panels (owner: Neil/Valaya, `backend/scripts/hc_seed.py`).**
   - No seeded case has any `sms_out` / `sms_in` or `callback_call` events, so the
     Messages panel is empty on every seed.
   - The seeded `caseworker_action` and `resident_response` events have empty payloads, so
     their timeline rows lack the action name and the answer.
   - Adding `{"action": …}`, `{"response": …}` and one confirmation `sms_out` per case
     would make the queue look lived-in.
7. **Full run-through, once 1–5 are done:**
   - a live intake call adds a row that slides into the queue
   - choosing an action and clicking **Call back** moves the button through
     Calling… → Resident answered → Done
   - the WhatsApp confirmation and receipt appear in the Messages panel

## How the call button decides its state

It is computed from the case's `events`, never from local clicks, so a reload or a
second screen shows the same thing (`src/lib/callback.ts`). Only events after the latest
`caseworker_action` count:

| Event seen | Button |
|---|---|
| none yet | Call back in {language} |
| `callback_call` without `stage: "ended"` (logged by `POST /callback`) | Calling… |
| `resident_response` | Resident answered (Done once it's 15+ min old with no post-call webhook) |
| `callback_call` with `stage: "ended"` (post-call webhook), plus an answer | Done |
| `callback_call` with `stage: "ended"` and no answer | No answer · Call again |
