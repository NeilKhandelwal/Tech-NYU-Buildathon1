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
  // null on cases filed before the line took all 311 types: those were all heat
  issue_type: IssueType | null;
  description: string | null;
};

// backend/app/hc.py ISSUE_TYPES (and agent/provision.py's enum): third copy, on purpose,
// since Python and TypeScript can't share one file
export type IssueType =
  | "heat_hot_water"
  | "missed_pickup"
  | "pothole"
  | "streetlight"
  | "water"
  | "animal"
  | "other";

export const ISSUE_LABEL: Record<IssueType, string> = {
  heat_hot_water: "No heat / hot water",
  missed_pickup: "Missed pickup",
  pothole: "Pothole",
  streetlight: "Streetlight out",
  water: "Water problem",
  animal: "Animal problem",
  other: "Other",
};

export const issueOf = (c: Case): IssueType => c.issue_type ?? "heat_hot_water";
export const isHeat = (c: Case) => issueOf(c) === "heat_hot_water";

export type EventType =
  | "intake_call"
  | "submitted"
  | "sms_out"
  | "sms_in"
  | "caseworker_action"
  | "callback_call"
  | "resident_response"
  | "note"
  // the live call log (backend/app/live.py): payload.conversation_id on each, and case_id
  // stays null until the agent files the case
  | "call_started"
  | "transcript"
  | "call_ended";

export const CALL_EVENT_TYPES: EventType[] = ["call_started", "transcript", "call_ended"];

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

// The heat templates are DASHBOARD.md's; every other 311 type gets these, with the problem and
// the place filled in (see messageTemplate).
const GENERIC_TEMPLATES: Record<CaseworkerAction, string> = {
  schedule_inspection: "A city crew will check the {issue} at {place} by Tuesday. Is there anything else we should know?",
  need_more_info: "We need one more detail: exactly where is the {issue}, and when did it start?",
  emergency_repair: "Your report has been sent for urgent repair. A crew may contact you. Is this the best number to reach you?",
  resolved: "The city reports the {issue} at {place} has been fixed. Is it resolved on your end?",
};

export function messageTemplate(action: CaseworkerAction, c: Case): string {
  if (isHeat(c)) return ACTIONS.find((a) => a.value === action)!.template;
  return GENERIC_TEMPLATES[action]
    .replace("{issue}", ISSUE_LABEL[issueOf(c)].toLowerCase())
    .replace("{place}", c.address);
}

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
