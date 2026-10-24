import type { CaseEvent, EventType } from "@/lib/types";
import { ACTIONS, RESPONSE_LABEL } from "@/lib/types";
import { nyClock } from "@/lib/format";
import { Card } from "./ui";

const KIND: Record<EventType, { icon: string; label: string; tone: string }> = {
  intake_call: { icon: "☎", label: "Intake call", tone: "bg-zinc-100 text-zinc-800" },
  submitted: { icon: "✓", label: "Resident confirmed and submitted", tone: "bg-zinc-100 text-zinc-800" },
  sms_out: { icon: "✉", label: "Message sent", tone: "bg-zinc-100 text-zinc-700" },
  sms_in: { icon: "✉", label: "Message received", tone: "bg-emerald-100 text-emerald-800" },
  caseworker_action: { icon: "✎", label: "Caseworker action", tone: "bg-yellow-400 text-zinc-950" },
  callback_call: { icon: "☎", label: "Callback call", tone: "bg-zinc-100 text-zinc-800" },
  resident_response: { icon: "↩", label: "Resident answered", tone: "bg-emerald-100 text-emerald-800" },
  note: { icon: "•", label: "Note", tone: "bg-zinc-100 text-zinc-700" },
  call_started: { icon: "☎", label: "Call started", tone: "bg-red-100 text-red-700" },
  transcript: { icon: "…", label: "Line", tone: "bg-zinc-100 text-zinc-700" },
  call_ended: { icon: "☎", label: "Call ended", tone: "bg-zinc-100 text-zinc-700" },
};

function detail(e: CaseEvent): string | null {
  const p = e.payload ?? {};
  if (e.type === "caseworker_action") return ACTIONS.find((a) => a.value === p.action)?.label ?? null;
  if (e.type === "resident_response") return RESPONSE_LABEL[p.response] ?? p.response ?? null;
  if (e.type === "callback_call") {
    if (e.payload?.stage === "ended" || e.text_original) return e.text_original ? "Call ended, transcript saved" : "Call ended";
    return "Dialing the resident";
  }
  if (e.type === "call_started" || e.type === "call_ended") return p.agent === "callback" ? "callback" : p.agent === "intake" ? "intake" : null;
  if (e.type === "sms_out" && p.status && p.status !== "sent") return "not delivered, shown here only";
  return null;
}

export function Timeline({ events, fresh }: { events: CaseEvent[]; fresh: Set<string> }) {
  // live transcript lines have their own card; one row per line would bury everything else
  const newest = events.filter((e) => e.type !== "transcript").reverse();
  return (
    <Card title="Timeline" aside={<span className="text-xs text-zinc-400">{newest.length} events</span>}>
      {newest.length === 0 && <p className="text-sm text-zinc-400">No events yet.</p>}
      <ol className="space-y-3">
        {newest.map((e) => {
          const k = KIND[e.type] ?? KIND.note;
          const extra = detail(e);
          // the callback transcript is long and already in the call's own record; keep the row short
          const showText = e.type !== "callback_call";
          return (
            <li key={e.id} className={`flex gap-3 ${fresh.has(e.id) ? "animate-arrive" : ""}`}>
              <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm ${k.tone}`}>{k.icon}</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2 text-sm">
                  <span className="font-semibold text-zinc-800">{k.label}</span>
                  {extra && <span className="text-zinc-500">· {extra}</span>}
                  <span className="ml-auto shrink-0 text-xs tabular-nums text-zinc-400">{nyClock(e.created_at)}</span>
                </div>
                {showText && e.text_en && <p className="text-sm text-zinc-700">{e.text_en}</p>}
                {showText && e.text_original && e.text_original !== e.text_en && (
                  <p className="text-sm text-zinc-400">{e.text_original}</p>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
