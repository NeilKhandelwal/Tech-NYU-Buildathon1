# CONTRACT.md — schema and API (source of truth)

Owner: Neil. Romir's agents and Andrew's dashboard build against this.
If you change it, tell the team chat.

## Supabase tables

```sql
create table cases (
  id uuid primary key default gen_random_uuid(),
  case_number text unique not null,            -- 'HC-' + 6 digits
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  status text not null default 'intake',
    -- intake | submitted | in_review | callback_requested | called_back | resolved
  resident_name text,
  resident_phone text not null,
  language text not null,                       -- BCP-47, e.g. 'hi', 'es', 'zh', 'bn', 'en'
  language_label text,                          -- 'Hindi'
  address text not null,
  unit text,
  borough text,
  lat numeric, lon numeric,
  days_without_heat int,
  indoor_f numeric,
  outdoor_f numeric,
  is_violation boolean,
  rule_applied text,                            -- e.g. 'Day rule: outside 41F < 55F, inside 61F < 68F'
  priority text default 'normal',               -- urgent | high | normal
  summary_en text,                              -- English summary for the caseworker
  transcript_original text,
  transcript_en text,
  building jsonb,                               -- {open_violations, heat_complaints_last_season, repeat_offender, source_urls[]}
  suggested_action text,                        -- AI suggestion: 'schedule_inspection' etc.
  caseworker_action text,                       -- schedule_inspection | need_more_info | emergency_repair | resolved
  caseworker_message_en text,                   -- what the caseworker approved to say
  resident_response text,                       -- confirmed | reschedule | question | no_answer
  resident_response_note_en text
);

create table events (
  id uuid primary key default gen_random_uuid(),
  case_id uuid references cases(id) on delete cascade,
  created_at timestamptz default now(),
  type text not null,
    -- intake_call | submitted | sms_out | sms_in | caseworker_action | callback_call | resident_response | note
  actor text,                                   -- resident | intake_agent | callback_agent | caseworker | system
  text_original text,                           -- in resident's language where relevant
  text_en text,
  payload jsonb
);

-- Realtime: enable for both tables.
alter publication supabase_realtime add table cases, events;
```

## Endpoints (Next.js `app/api`)

All agent tool endpoints accept JSON and return JSON quickly (< 2 s). Long work, like translation, happens in the post-call webhook.

### Called by the intake agent (ElevenLabs server tools)
```
POST /api/tools/create_case
  in:  {resident_name, resident_phone, language, language_label, address, unit, days_without_heat, indoor_f}
  out: {case_id, case_number}
  does: inserts case (status 'intake'), geocodes address (best effort), event 'intake_call'

POST /api/tools/check_violation
  in:  {case_id}            (uses case indoor_f, lat/lon, current NYC time)
  out: {is_violation, outdoor_f, rule_applied, fine_range: "$250-$500 per day"}
  does: Open-Meteo lookup -> rule -> updates case

POST /api/tools/submit_case
  in:  {case_id, resident_confirmed: true}
  out: {ok, case_number}
  does: status 'submitted', event 'submitted', sends confirmation SMS in resident's language
        (only callable after the resident says yes)
```

### Called by the callback agent
```
POST /api/tools/record_resident_response
  in:  {case_id, response: "confirmed"|"reschedule"|"question", note_original, note_en}
  out: {ok}
  does: updates case (status 'called_back'), event 'resident_response', sends receipt SMS
```

### Webhooks
```
POST /api/webhooks/elevenlabs_post_call
  ElevenLabs post-call payload (verify signature if time allows)
  does: find case by conversation/caller -> save transcript_original ->
        LLM: transcript_en, summary_en, priority, suggested_action ->
        building_lookup (Open Data, cached for demo building) ->
        status 'submitted' -> 'in_review'
        For callback calls: save event 'callback_call' with transcript.

POST /api/webhooks/twilio_sms
  Twilio inbound SMS -> event 'sms_in' (translate to English) -> show on dashboard
```

### Called by the dashboard
```
GET  /api/cases                   -> list (or read Supabase directly with anon key + RLS off for demo)
POST /api/cases/:id/action
  in:  {caseworker_action, caseworker_message_en}
  out: {ok}
  does: event 'caseworker_action', status 'callback_requested'

POST /api/cases/:id/callback
  in:  {}
  out: {ok, conversation_id}
  does: ElevenLabs outbound call (Twilio) to resident_phone using the callback agent with dynamic variables:
        resident_name, language, language_label, case_number, address, caseworker_action,
        caseworker_message_en
```

### Internal helpers (`/lib`)
- `sendSms(to, text)`: Twilio. It logs an `sms_out` event even if sending fails, so the panel still shows it.
- `translate(text, targetLang)`: LLM. Used for SMS bodies and transcripts.
- `buildingLookup(address)`: NYC Open Data (HPD violations and 311 heat complaints). Check the cache first.
- `outdoorTemp(lat, lon, at)`: Open-Meteo.
- `newCaseNumber()`: `HC-` + 6 random digits.

## SMS templates (English source; translate to the resident's language before sending)
- Confirmation: "Heat Check: your no-heat complaint {case_number} was sent to the city caseworker. We'll call you back in {language_label}."
- Receipt: "Heat Check {case_number}: {caseworker_action_plain}. Your answer: {response_plain}. Reply anytime."
