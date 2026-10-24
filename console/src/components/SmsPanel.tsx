"use client";

import { useState } from "react";
import type { CaseEvent } from "@/lib/types";
import { sendText } from "@/lib/api";
import { nyClock } from "@/lib/format";
import { Card } from "./ui";

// The resident's phone, as the resident sees it: CityLine on the left, their replies on the
// right. Every sms_out is logged even when Twilio refuses it, so this is also the fallback.
// The events say sms_* but the demo sends over the Twilio WhatsApp sandbox
// (TWILIO_CHANNEL=whatsapp); the event doesn't record which, so the title names both.
export function SmsPanel({
  caseId,
  languageLabel,
  events,
  phone,
  fresh,
}: {
  caseId: string;
  languageLabel: string;
  events: CaseEvent[];
  phone: string;
  fresh: Set<string>;
}) {
  const texts = events.filter((e) => e.type === "sms_out" || e.type === "sms_in");
  return (
    <Card title="Messages · WhatsApp / SMS" aside={<span className="font-mono text-xs text-slate-400">…{phone.slice(-4)}</span>}>
      <div className="mx-auto max-w-sm rounded-[1.75rem] border-4 border-slate-800 bg-slate-50 p-3">
        <div className="mb-2 text-center text-[11px] font-semibold text-slate-500">CityLine</div>
        <div className="flex max-h-80 min-h-32 flex-col gap-2 overflow-y-auto">
          {texts.length === 0 && <p className="py-6 text-center text-xs text-slate-400">No messages yet.</p>}
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
                  {e.payload?.voice_note === true && (
                    <div className={`mb-1 text-[10px] font-semibold uppercase tracking-wide ${mine ? "text-blue-200" : "text-slate-500"}`}>
                      🎤 Voice note · transcribed by ElevenLabs
                    </div>
                  )}
                  {e.payload?.kind === "ack" && (
                    <div className="mb-1 text-[10px] font-semibold text-emerald-700">✓ Auto-reply</div>
                  )}
                  {e.payload?.kind === "caseworker" && (
                    <div className="mb-1 text-[10px] font-semibold text-slate-500">Caseworker</div>
                  )}
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
      <TextResident caseId={caseId} languageLabel={languageLabel} />
    </Card>
  );
}

// The caseworker writes English and clicks Send: that click is the human check. The API
// translates and sends it; the new sms_out shows up in the thread on the next poll.
function TextResident({ caseId, languageLabel }: { caseId: string; languageLabel: string }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    setSending(true);
    setError(null);
    try {
      await sendText(caseId, text.trim());
      setText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="mt-3 space-y-2">
      <label className="block text-xs font-medium text-slate-600">
        Text resident · write in English, sent in {languageLabel}
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          placeholder="Is your apartment still cold?"
          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-700 focus:outline-none"
        />
      </label>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={send}
          disabled={sending || !text.trim()}
          className="rounded-lg bg-blue-900 px-3 py-1.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          {sending ? "Sending…" : `Send in ${languageLabel}`}
        </button>
        {error && <span className="text-xs font-medium text-red-700">{error}</span>}
      </div>
    </div>
  );
}
