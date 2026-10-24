import type { CaseEvent } from "@/lib/types";
import { nyClock } from "@/lib/format";
import { Card } from "./ui";

// The resident's phone, as the resident sees it: Heat Check on the left, their replies on the
// right. Every sms_out is logged even when Twilio refuses it, so this is also the fallback.
export function SmsPanel({ events, phone, fresh }: { events: CaseEvent[]; phone: string; fresh: Set<string> }) {
  const texts = events.filter((e) => e.type === "sms_out" || e.type === "sms_in");
  return (
    <Card title="SMS" aside={<span className="font-mono text-xs text-slate-400">…{phone.slice(-4)}</span>}>
      <div className="mx-auto max-w-sm rounded-[1.75rem] border-4 border-slate-800 bg-slate-50 p-3">
        <div className="mb-2 text-center text-[11px] font-semibold text-slate-500">Heat Check</div>
        <div className="flex max-h-80 min-h-32 flex-col gap-2 overflow-y-auto">
          {texts.length === 0 && <p className="py-6 text-center text-xs text-slate-400">No texts yet.</p>}
          {texts.map((e) => {
            const mine = e.type === "sms_in"; // "mine" from the resident's side of the phone
            const status = e.payload?.status as string | undefined;
            const english = e.text_en && e.text_en !== e.text_original ? e.text_en : null;
            return (
              <div key={e.id} className={`flex flex-col ${fresh.has(e.id) ? "animate-arrive" : ""} ${mine ? "items-end" : "items-start"}`}>
                <div
                  className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm leading-snug ${
                    mine ? "rounded-br-sm bg-blue-900 text-white" : "rounded-bl-sm bg-white text-slate-900 ring-1 ring-slate-200"
                  }`}
                >
                  {e.text_original ?? e.text_en}
                  {english && (
                    <div className={`mt-1 border-t pt-1 text-xs ${mine ? "border-blue-700 text-blue-100" : "border-slate-100 text-slate-500"}`}>
                      EN: {english}
                    </div>
                  )}
                </div>
                <span className="mt-0.5 px-1 text-[10px] text-slate-400">
                  {nyClock(e.created_at)}
                  {!mine && status && status !== "sent" && (
                    <span className="ml-1 font-semibold text-amber-700">· {status === "skipped" ? "not sent (Twilio off)" : "not delivered"}</span>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </Card>
  );
}
