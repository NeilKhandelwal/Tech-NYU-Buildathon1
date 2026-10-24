# Heat Check Caseworker Console (demo)

The government side of Heat Check: the caseworker queue, case detail, and the
"Call back in the resident's language" action. Spec: `DASHBOARD.md` (on `main`).

- **Reads** come straight from Supabase (`cases`, `events`), live over Realtime.
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

## Before the demo (open items, 2026-10-24)

Lint and build pass. The layout has been checked against the 4 seeded cases on the Fly
API, but nothing has run through Supabase (reads and Realtime) yet.

1. **Publishable key.** Put the full `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` (Neil has it)
   in `console/.env.local` and in the Vercel project. Without it the page shows
   "Supabase isn't configured".
2. **Row-level security on `cases` / `events`.** If the console shows "Could not read
   cases", Supabase has RLS on and the publishable key can't read. For the demo, either
   turn RLS off for these two tables or add a read policy:
   `create policy "demo read" on cases for select using (true);` (and the same for
   `events`). Realtime respects RLS too, so this is also what makes live updates work.
3. **Realtime publication.** `supabase/schema.sql` runs
   `alter publication supabase_realtime add table cases, events;`. If the header's dot stays
   on "Connecting", or new cases only appear after a reload, check that it ran.
4. **Call back, end to end.** `POST /api/cases/{id}/action` and `/callback` haven't been
   exercised from the console yet. `/callback` needs `ELEVENLABS_CALLBACK_AGENT_ID` and
   `ELEVENLABS_PHONE_NUMBER_ID` on Fly. If they're missing, the button shows the API's 503
   message.
5. **Full run-through.** Once 1–3 are done:
   - a live intake call adds a row that slides into the queue
   - choosing an action and clicking **Call back** moves the button through
     Calling… → Resident answered → Done
   - the texts appear in the SMS panel, including any marked "not delivered" if
     Twilio blocks sending

## How the call button decides its state

It is computed from the case's `events`, never from local clicks, so a reload or a
second screen shows the same thing (`src/lib/callback.ts`). Only events after the latest
`caseworker_action` count:

| Event seen | Button |
|---|---|
| none yet | Call back in {language} |
| `callback_call` with no transcript (logged by `POST /callback`) | Calling… |
| `resident_response` | Resident answered (Done once it's 15+ min old with no post-call webhook) |
| `callback_call` with a transcript (post-call webhook), plus an answer | Done |
| `callback_call` with a transcript and no answer | No answer · Call again |
