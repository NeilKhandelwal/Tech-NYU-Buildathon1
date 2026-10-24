# DASHBOARD.md — caseworker console spec

Owner: Andrew. Next.js App Router + Supabase Realtime, deployed on Vercel.
- **Header title:** "Heat Check Caseworker Console (demo)".
- **Branding:** no NYC or HPD logos or branding. Use our own simple mark.

This screen is on the projector for the whole demo. It should look like a real ops tool: dense, calm, readable from the back of the room.

## Layout (desktop, 1440px wide; it doesn't need to work on mobile)
```
┌───────────────────────────────────────────────────────────────────────────┐
│ Heat Check Caseworker Console (demo)      ● Live    Open 5   Urgent 1   Avg wait 6m │
├───────────────┬───────────────────────────────────────────────────────────┤
│ QUEUE         │ CASE HC-482913 · Hindi · 2800 Creston Ave 4B, Bronx        │
│ [filter: all] │ Status: In review   Priority: HIGH   Violation: YES        │
│               │                                                           │
│ HC-482913 ●new│ Summary (EN)         | Violation check                    │
│ Hindi · Bronx │  3 days no heat...   |  61F inside, 41F outside, 2:14pm   │
│ YES · HIGH    │                      |  Day rule: needs 68F → VIOLATION   │
│ 2 min ago     │ Building history     |  Fine exposure $250-$500/day       │
│ ...           │  12 open violations, 31 heat complaints last season,      │
│               │  repeat offender                                          │
│               │ Transcript  [Original | English]  (tabs)                  │
│               │ Timeline (events, newest first)                           │
│               ├───────────────────────────────────────────────────────────┤
│               │ ACTION  (Schedule inspection ▾)   AI suggests: inspection │
│               │ Message to resident (English, editable):                  │
│               │ [ An inspector will visit Tuesday 10am-2pm. Will you be  ]│
│               │ [ home?                                                   ]│
│               │ Will be spoken in Hindi.   [ Call back in Hindi ▶ ]       │
└───────────────┴───────────────────────────────────────────────────────────┘
```

## Components
1. **Header stats:** open cases, urgent count, live indicator.
2. **Queue:**
   - Each row shows case number, a language badge, borough, violation YES/NO badge, priority, status and age.
   - New cases slide in at the top with a highlight.
   - Sort order: urgent, then high, then newest.
3. **Case header:** number, language, address, status, priority, violation.
4. **Summary card:** `summary_en`.
5. **Violation card:**
   - indoor and outdoor temperature, time, `rule_applied`, YES/NO, fine range
   - a small bar: indoor temperature vs the legal minimum
6. **Building history card:** open violations, heat complaints last season, repeat-offender badge.
7. **Transcript:** tabs for Original (resident's language) and English.
8. **Timeline:** `events` for the case, live. Each event has an icon by type and its text, in English plus the original.
9. **Action panel:**
   - Action select: Schedule inspection, Need more info, Emergency repair referral, Resolved.
   - Message box, prefilled from a template per action.
   - Shows the line "Will be spoken in {language_label}."
   - "Call back in {language_label}" button. It saves the action (`POST /api/cases/:id/action`), then calls `POST /api/cases/:id/callback`.
   - Button states: idle, "Calling…", "Resident answered", "Done".
10. **SMS panel:** a phone-style list of `sms_out` and `sms_in` events, each with its original text and English. This is the fallback if Twilio is blocked.

## Message templates (prefill)
- **Schedule inspection:** "An inspector will visit your apartment on Tuesday between 10am and 2pm. Will you be home?"
- **Need more info:** "We need one more detail: is the heat out in the whole building or only your apartment?"
- **Emergency repair referral:** "Your case has been sent for emergency repair. A contractor may contact you. Is this the best number to reach you?"
- **Resolved:** "Your landlord reports the heat is restored. Is your apartment warm now?"

## Realtime
- Subscribe to `cases` (insert/update) and `events` (insert).
- No page refreshes during the demo.
- Highlight changed fields for about 2 s.

## Styling
- Neutral light theme with one accent (deep blue). Red is only for violations and urgent.
- Large type for the case header, readable from the back of the room.
- Use Tailwind + shadcn/ui if already set up. Don't spend time on polish before 2:30.

## Demo data
Valaya seeds 4–5 older cases (Spanish, Mandarin, Bengali, English) in various statuses so the queue looks real. The live Hindi call becomes the new top case.
