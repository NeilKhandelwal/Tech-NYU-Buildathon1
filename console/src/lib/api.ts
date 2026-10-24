import type { CaseworkerAction } from "./types";

export const API = (process.env.NEXT_PUBLIC_API_URL ?? "https://heatcheck-nyu.fly.dev").replace(/\/$/, "");

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

// The caseworker's own message, written in English; the backend translates and sends it
export function sendText(caseId: string, textEn: string) {
  return post(`/api/cases/${caseId}/sms`, { text_en: textEn });
}
