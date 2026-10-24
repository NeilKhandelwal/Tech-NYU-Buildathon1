"use client";

import { useState } from "react";
import type { CaseEvent } from "@/lib/types";
import { API, sendText } from "@/lib/api";
import { nyClock } from "@/lib/format";
import { Card } from "./ui";

const KIND_LABEL: Record<string, string> = { caseworker: "Caseworker", photo_request: "Photo request" };

// The resident's phone, as the resident sees it: Heat Check on the left, their replies on the
// right. Every sms_out is logged even when Twilio refuses it, so this is also the fallback.
// The events say sms_* but the demo sends over the Twilio WhatsApp sandbox
// (TWILIO_CHANNEL=whatsapp); the event doesn't record which, so the title names both.
export function SmsPanel({ caseId, events, phone, languageLabel, fresh }: {
  caseId: string;
  events: CaseEvent[];
  phone: string;
  languageLabel: string;
  fresh: Set<string>;
}) {
  const texts = events.filter((e) => e.type === "sms_out" || e.type === "sms_in");
  return (
    <Card title="Messages · WhatsApp / SMS" aside={<span className="font-mono text-xs text-slate-400">…{phone.slice(-4)}</span>}>
      <div className="mx-auto max-w-sm rounded-[1.75rem] border-4 border-slate-800 bg-slate-50 p-3">
        <div className="mb-2 text-center text-[11px] font-semibold text-slate-500">Heat Check</div>
        <div className="flex max-h-80 min-h-32 flex-col gap-2 overflow-y-auto">
          {texts.length === 0 && <p className="py-6 text-center text-xs text-slate-400">No messages yet.</p>}
          {texts.map((e) => {
            const mine = e.type === "sms_in"; // "mine" from the resident's side of the phone
            const status = e.payload?.status as string | undefined;
            const kind = KIND_LABEL[e.payload?.kind as string];
            const media = (e.payload?.media ?? []) as { url: string; content_type?: string }[];
            const text = e.text_original || e.text_en;
            const english = e.text_en && e.text_en !== e.text_original ? e.text_en : null;
            return (
              <div key={e.id} className={`flex flex-col ${fresh.has(e.id) ? "animate-arrive" : ""} ${mine ? "items-end" : "items-start"}`}>
                <div
                  className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm leading-snug ${
                    mine ? "rounded-br-sm bg-blue-900 text-white" : "rounded-bl-sm bg-white text-slate-900 ring-1 ring-slate-200"
                  }`}
                >
                  {!mine && kind && (
                    <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400">{kind}</div>
                  )}
                  {media.map((m, i) => (
                    // a plain <img>: the photo is proxied by our API, so next/image would need its host configured
                    <a key={i} href={API + m.url} target="_blank" rel="noreferrer">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={API + m.url} alt="Photo from the resident" className={`max-w-full rounded-lg ${text ? "mb-1" : ""}`} />
                    </a>
                  ))}
                  {text}
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
      <Composer caseId={caseId} languageLabel={languageLabel} />
    </Card>
  );
}

// The caseworker writes English; the backend translates it into the resident's language and
// sends it. Typing and clicking Send is the human check, so nothing is sent automatically.
function Composer({ caseId, languageLabel }: { caseId: string; languageLabel: string }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    setSending(true);
    setError(null);
    try {
      await sendText(caseId, text.trim());
      setText(""); // the sms_out event arrives over realtime, so the thread updates itself
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not send");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="mt-3 space-y-1.5">
      <label htmlFor="sms-composer" className="block text-xs text-slate-500">
        Write in English · sent to the resident in {languageLabel}
      </label>
      <textarea
        id="sms-composer"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={2}
        placeholder="e.g. Can you send a photo of your thermostat?"
        className="w-full resize-none rounded border border-slate-300 px-2 py-1.5 text-sm focus:border-blue-900 focus:outline-none"
      />
      <div className="flex items-center gap-2">
        <button
          onClick={send}
          disabled={sending || !text.trim()}
          className="rounded bg-blue-900 px-3 py-1.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          {sending ? "Sending…" : `Send in ${languageLabel}`}
        </button>
        {error && <span className="text-xs text-red-700">{error}</span>}
      </div>
    </div>
  );
}
