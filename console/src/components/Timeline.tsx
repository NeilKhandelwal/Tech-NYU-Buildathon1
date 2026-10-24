import type { CaseEvent, EventType } from "@/lib/types";
import { ACTIONS, RESPONSE_LABEL } from "@/lib/types";
import { nyClock } from "@/lib/format";
import { Card } from "./ui";

const KIND: Record<EventType, { icon: string; label: string; tone: string }> = {
  intake_call: { icon: "☎", label: "Intake call", tone: "bg-blue-100 text-blue-900" },
  submitted: { icon: "✓", label: "Resident confirmed and submitted", tone: "bg-blue-100 text-blue-900" },
  sms_out: { icon: "✉", label: "Text sent", tone: "bg-slate-100 text-slate-700" },
  sms_in: { icon: "✉", label: "Text received", tone: "bg-emerald-100 text-emerald-800" },
  caseworker_action: { icon: "✎", label: "Caseworker action", tone: "bg-blue-900 text-white" },
  callback_call: { icon: "☎", label: "Callback call", tone: "bg-blue-100 text-blue-900" },
  resident_response: { icon: "↩", label: "Resident answered", tone: "bg-emerald-100 text-emerald-800" },
  note: { icon: "•", label: "Note", tone: "bg-slate-100 text-slate-700" },
};

function detail(e: CaseEvent): string | null {
  const p = e.payload ?? {};
  if (e.type === "caseworker_action") return ACTIONS.find((a) => a.value === p.action)?.label ?? null;
  if (e.type === "resident_response") return RESPONSE_LABEL[p.response] ?? p.response ?? null;
  if (e.type === "callback_call") return e.text_original ? "Call ended, transcript saved" : "Dialing the resident";
  if (e.type === "sms_out" && p.status && p.status !== "sent") return "not delivered, shown here only";
  return null;
}

export function Timeline({ events }: { events: CaseEvent[] }) {
  const newest = [...events].reverse();
  return (
    <Card title="Timeline" aside={<span className="text-xs text-slate-400">{events.length} events</span>}>
      {newest.length === 0 && <p className="text-sm text-slate-400">No events yet.</p>}
      <ol className="space-y-3">
        {newest.map((e) => {
          const k = KIND[e.type] ?? KIND.note;
          const extra = detail(e);
          // the callback transcript is long and already in the call's own record; keep the row short
          const showText = e.type !== "callback_call";
          return (
            <li key={e.id} className="flex gap-3 animate-arrive">
              <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm ${k.tone}`}>{k.icon}</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2 text-sm">
                  <span className="font-semibold text-slate-800">{k.label}</span>
                  {extra && <span className="text-slate-500">· {extra}</span>}
                  <span className="ml-auto shrink-0 text-xs tabular-nums text-slate-400">{nyClock(e.created_at)}</span>
                </div>
                {showText && e.text_en && <p className="text-sm text-slate-700">{e.text_en}</p>}
                {showText && e.text_original && e.text_original !== e.text_en && (
                  <p className="text-sm text-slate-400">{e.text_original}</p>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
