import type { Case, CaseEvent, CaseworkerAction } from "./types";

const API = (process.env.NEXT_PUBLIC_API_URL ?? "https://heatcheck-nyu.fly.dev").replace(/\/$/, "");

// Reads go through the API, not Supabase: the API reads with full access, while the publishable
// key sees nothing while row level security is on (useConsole polls these).
async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!r.ok) throw new ApiError(r.status, `GET ${path}: HTTP ${r.status}`);
  return r.json();
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export const getCases = () => get<Case[]>("/api/cases");
export const getCase = (id: string) => get<Case & { events: CaseEvent[] }>(`/api/cases/${id}`);
// 404 until the API is redeployed with the live call log (backend/app/live.py)
export const getCalls = (hours: number) => get<CaseEvent[]>(`/api/calls?hours=${hours}`);

async function post(path: string, body: unknown) {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  // FastAPI errors carry {detail}; show it, since it names the cause (409, 503 not configured…)
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${r.status}`);
  return data;
}

export function saveAction(caseId: string, action: CaseworkerAction, message: string) {
  return post(`/api/cases/${caseId}/action`, {
    caseworker_action: action,
    caseworker_message_en: message,
  });
}

export function startCallback(caseId: string): Promise<{ ok: boolean; conversation_id: string }> {
  return post(`/api/cases/${caseId}/callback`, {});
}

// The caseworker's own words, in English; the API translates them into the resident's language
// and logs the sms_out, which the next poll shows in the panel
export function sendText(caseId: string, textEn: string): Promise<CaseEvent> {
  return post(`/api/cases/${caseId}/sms`, { text_en: textEn });
}
