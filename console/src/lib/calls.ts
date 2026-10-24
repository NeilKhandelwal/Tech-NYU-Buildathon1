import { parseTime } from "./format";
import type { CaseEvent } from "./types";

export type Line = {
  id: string;
  at: string;
  who: "resident" | "agent";
  original: string;
  english: string | null; // null while the translation is on its way (or no LLM is set)
};

export type Call = {
  conversationId: string;
  agent: "intake" | "callback" | "other";
  startedAt: string;
  endedAt: string | null;
  caseId: string | null;
  caller: string | null;
  lines: Line[];
  live: boolean;
};

// A call whose end never arrived (the backend restarted mid-call) stops counting as live.
const STALE_MS = 30 * 60_000;

// Group the live call log (call_started / transcript / call_ended rows) into calls, newest first.
export function groupCalls(events: CaseEvent[], now: number): Call[] {
  const byId = new Map<string, Call>();
  for (const e of [...events].sort((a, b) => a.created_at.localeCompare(b.created_at))) {
    const id = e.payload?.conversation_id as string | undefined;
    if (!id) continue;
    let call = byId.get(id);
    if (!call) {
      call = {
        conversationId: id,
        agent: (e.payload?.agent as Call["agent"]) ?? "other",
        startedAt: e.created_at,
        endedAt: null,
        caseId: null,
        caller: null,
        lines: [],
        live: false,
      };
      byId.set(id, call);
    }
    call.caseId ??= e.case_id;
    if (e.type === "call_started") {
      call.startedAt = e.created_at;
      call.caller = (e.payload?.caller as string | null) ?? null;
    } else if (e.type === "call_ended") {
      call.endedAt = e.created_at;
    } else if (e.type === "transcript" && e.text_original) {
      call.lines.push({
        id: e.id,
        at: e.created_at,
        who: e.actor === "resident" ? "resident" : "agent",
        original: e.text_original,
        english: e.text_en,
      });
    }
  }
  const calls = [...byId.values()];
  for (const c of calls) c.live = !c.endedAt && now - parseTime(c.startedAt) < STALE_MS;
  return calls.sort((a, b) => Number(b.live) - Number(a.live) || b.startedAt.localeCompare(a.startedAt));
}

export function elapsed(from: string, to: number) {
  const s = Math.max(0, Math.round((to - parseTime(from)) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export const AGENT_LABEL: Record<Call["agent"], string> = {
  intake: "Intake call",
  callback: "Callback call",
  other: "Call",
};
