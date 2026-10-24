// Row shapes from the `create table` statements in CONTRACT.md (supabase/schema.sql).
// numeric columns arrive as numbers from PostgREST; jsonb arrives parsed.

export type Status =
  | "intake"
  | "submitted"
  | "in_review"
  | "callback_requested"
  | "called_back"
  | "resolved";

export type Priority = "urgent" | "high" | "normal";

export type CaseworkerAction =
  | "schedule_inspection"
  | "need_more_info"
  | "emergency_repair"
  | "resolved";

export type Building = {
  found?: boolean;
  open_violations?: number;
  open_class_c?: number;
  heat_violations?: number;
  heat_complaints_last_season?: number;
  repeat_offender?: boolean;
  recent?: { date: string; class: string; status: string; description: string }[];
  source_urls?: string[];
  source?: string;
};

export type Case = {
  id: string;
  case_number: string;
  created_at: string;
  updated_at: string;
  status: Status;
  resident_name: string | null;
  resident_phone: string;
  language: string;
  language_label: string | null;
  address: string;
  unit: string | null;
  borough: string | null;
  days_without_heat: number | null;
  indoor_f: number | null;
  outdoor_f: number | null;
  is_violation: boolean | null;
  rule_applied: string | null;
  priority: Priority | null;
  summary_en: string | null;
  transcript_original: string | null;
  transcript_en: string | null;
  building: Building | null;
  suggested_action: CaseworkerAction | null;
  caseworker_action: CaseworkerAction | null;
  caseworker_message_en: string | null;
  resident_response: "confirmed" | "reschedule" | "question" | "no_answer" | null;
  resident_response_note_en: string | null;
};

export type EventType =
  | "intake_call"
  | "submitted"
  | "sms_out"
  | "sms_in"
  | "caseworker_action"
  | "callback_call"
  | "resident_response"
  | "note";

export type CaseEvent = {
  id: string;
  case_id: string;
  created_at: string;
  type: EventType;
  actor: string | null;
  text_original: string | null;
  text_en: string | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- jsonb differs per event type
  payload: Record<string, any> | null;
};

export const STATUS_LABEL: Record<Status, string> = {
  intake: "Intake",
  submitted: "Submitted",
  in_review: "In review",
  callback_requested: "Callback requested",
  called_back: "Called back",
  resolved: "Resolved",
};

export const ACTIONS: { value: CaseworkerAction; label: string; template: string }[] = [
  {
    value: "schedule_inspection",
    label: "Schedule inspection",
    template:
      "An inspector will visit your apartment on Tuesday between 10am and 2pm. Will you be home?",
  },
  {
    value: "need_more_info",
    label: "Need more info",
    template:
      "We need one more detail: is the heat out in the whole building or only your apartment?",
  },
  {
    value: "emergency_repair",
    label: "Emergency repair referral",
    template:
      "Your case has been sent for emergency repair. A contractor may contact you. Is this the best number to reach you?",
  },
  {
    value: "resolved",
    label: "Resolved",
    template: "Your landlord reports the heat is restored. Is your apartment warm now?",
  },
];

export const RESPONSE_LABEL: Record<string, string> = {
  confirmed: "Confirmed",
  reschedule: "Needs another time",
  question: "Has a question",
  no_answer: "No answer",
};
