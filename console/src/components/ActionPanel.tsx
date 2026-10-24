"use client";

import { useState } from "react";
import { saveAction, startCallback } from "@/lib/api";
import { callState, type CallState } from "@/lib/callback";
import { ACTIONS, messageTemplate, type Case, type CaseEvent, type CaseworkerAction } from "@/lib/types";

// Keyed by case id in the page, so switching cases starts from that case's saved values.
export function ActionPanel({ c, events }: { c: Case; events: CaseEvent[] }) {
  const initial = c.caseworker_action ?? c.suggested_action ?? "schedule_inspection";
  const [action, setAction] = useState<CaseworkerAction>(initial);
  const [message, setMessage] = useState(c.caseworker_message_en ?? messageTemplate(initial, c));
  const [dirty, setDirty] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const derived = callState(events);
  // from the click until the callback_call event lands, the data still says idle
  const state: CallState | "sending" = sending && derived === "idle" ? "sending" : derived;
  const lang = c.language_label ?? c.language;
  const notSubmitted = c.status === "intake";

  function pick(a: CaseworkerAction) {
    setAction(a);
    setMessage(messageTemplate(a, c));
    setDirty(true);
  }

  async function callBack() {
    setError(null);
    setSending(true);
    try {
      await saveAction(c.id, action, message.trim());
      await startCallback(c.id);
      setDirty(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  // a live call always locks the button; a finished one locks it until the message changes
  const live = state === "sending" || state === "calling" || state === "answered";
  const shown: CallState | "sending" = live || !dirty ? state : "idle";
  const disabled = notSubmitted || !message.trim() || live || shown === "done";

  return (
    <section className="border-t-2 border-blue-900 bg-white px-6 py-3">
      <div className="flex items-center gap-4">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500">Action</h3>
        <select
          value={action}
          onChange={(e) => pick(e.target.value as CaseworkerAction)}
          className="rounded border border-slate-300 bg-white px-3 py-1.5 text-[15px] font-medium text-slate-900 focus:border-blue-900 focus:outline-none"
        >
          {ACTIONS.map((a) => (
            <option key={a.value} value={a.value}>{a.label}</option>
          ))}
        </select>
        {c.suggested_action && (
          <span className="text-sm text-slate-500">
            AI suggests:{" "}
            <button
              onClick={() => pick(c.suggested_action!)}
              className="font-semibold text-blue-900 underline decoration-dotted underline-offset-2"
            >
              {ACTIONS.find((a) => a.value === c.suggested_action)?.label}
            </button>
          </span>
        )}
      </div>
      <label className="mt-3 block text-xs text-slate-500" htmlFor="msg">
        Message to resident (English, editable)
      </label>
      <div className="mt-1 flex items-stretch gap-4">
        <textarea
          id="msg"
          value={message}
          onChange={(e) => {
            setMessage(e.target.value);
            setDirty(true);
          }}
          rows={2}
          className="min-w-0 flex-1 resize-none rounded border border-slate-300 px-3 py-2 text-[16px] leading-snug text-slate-900 focus:border-blue-900 focus:outline-none"
        />
        <div className="flex w-72 shrink-0 flex-col items-end justify-between gap-2">
          <span className="text-sm text-slate-600">
            Will be spoken in <span className="font-semibold text-slate-900">{lang}</span>.
          </span>
          <CallButton state={shown} lang={lang} disabled={disabled} onClick={callBack} />
        </div>
      </div>
      {notSubmitted && <p className="mt-2 text-sm text-amber-700">The resident hasn&apos;t submitted this case yet.</p>}
      {error && <p className="mt-2 text-sm font-medium text-red-700">Couldn&apos;t call: {error}</p>}
    </section>
  );
}

function CallButton({ state, lang, disabled, onClick }: {
  state: CallState | "sending";
  lang: string;
  disabled: boolean;
  onClick: () => void;
}) {
  const look: Record<CallState | "sending", [string, string]> = {
    idle: [`Call back in ${lang} ▶`, "bg-blue-900 text-white hover:bg-blue-800"],
    sending: ["Calling…", "bg-blue-900 text-white"],
    calling: ["Calling…", "bg-blue-900 text-white"],
    answered: ["Resident answered", "bg-emerald-600 text-white"],
    done: ["Done ✓", "bg-emerald-50 text-emerald-800 ring-1 ring-emerald-300"],
    no_answer: [`No answer · Call again in ${lang}`, "bg-amber-500 text-white hover:bg-amber-600"],
  };
  const [label, style] = look[state];
  const busy = state === "sending" || state === "calling";
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`flex items-center gap-2 rounded-md px-5 py-2.5 text-base font-semibold transition-colors disabled:cursor-not-allowed ${style} ${
        disabled && state === "idle" ? "opacity-40" : ""
      }`}
    >
      {busy && <span className="h-3 w-3 animate-ping rounded-full bg-white/80" />}
      {label}
    </button>
  );
}
