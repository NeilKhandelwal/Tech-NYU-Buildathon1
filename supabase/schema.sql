-- Heat Check schema: Romir's CONTRACT.md (origin/docs/final-context) verbatim, plus one index.
-- Run once in the Supabase SQL editor. The FastAPI service never creates or alters tables here.

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

-- the post-call webhook finds a call's case by the conversation id logged on its event
create index if not exists events_conversation_id on events ((payload->>'conversation_id'));

-- Demo only: the dashboard reads both tables with the anon key over Realtime. With row level
-- security on (newer Supabase projects can enable it by default), every read and realtime event
-- would silently return nothing. Off means anyone with the anon key can read these demo rows.
alter table cases disable row level security;
alter table events disable row level security;
