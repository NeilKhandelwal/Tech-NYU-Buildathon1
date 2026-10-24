"use client";

import { useState } from "react";
import { saveAction, startCallback } from "@/lib/api";
import { callState, type CallState } from "@/lib/callback";
import { ACTIONS, RESPONSE_LABEL, messageTemplate, type Case, type CaseEvent, type CaseworkerAction } from "@/lib/types";

const label = (a: CaseworkerAction) => ACTIONS.find((x) => x.value === a)!.label;

// The one decision on this case: what to do, what to say, and the call that says it.
// Keyed by case id in the page, so switching cases starts from that case's saved values.
export function ActionPanel({ c, events }: { c: Case; events: CaseEvent[] }) {
  const suggested = c.suggested_action;
  const initial = c.caseworker_action ?? suggested ?? "schedule_inspection";
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
  const changed = !!suggested && action !== suggested;

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

  // a live call always holds the footer; a finished one holds it until the message changes
  const live = state === "sending" || state === "calling" || state === "answered";
  const shown: CallState | "sending" = live || !dirty ? state : "idle";
  const canCall = !notSubmitted && !!message.trim() && !live;

  return (
    <section className="border-t border-slate-300 bg-white">
      <div className="px-6 pt-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500">Next step</h3>
          {changed && <span className="text-xs font-semibold text-amber-700">Changed from the suggestion</span>}
          {!changed && suggested && <span className="text-xs text-slate-500">Suggested from the call</span>}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-6 gap-y-2">
          <h2 className="text-[26px] font-bold leading-tight tracking-tight text-slate-900">{label(action)}</h2>
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Choose a different next step">
            <span className="mr-1 text-xs text-slate-500">Or instead:</span>
            {ACTIONS.filter((a) => a.value !== action).map((a) => (
              <button
                key={a.value}
                onClick={() => pick(a.value)}
                disabled={live}
                className="min-h-9 rounded-full border border-slate-300 bg-white px-3 text-[13px] text-slate-700 transition-colors hover:border-blue-900 hover:text-blue-900 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {a.label}
              </button>
            ))}
            {changed && (
              <button
                onClick={() => pick(suggested!)}
                disabled={live}
                className="min-h-9 rounded-full border border-blue-900 bg-white px-3 text-[13px] font-semibold text-blue-900 disabled:opacity-40"
              >
                Back to suggestion
              </button>
            )}
          </div>
        </div>

        <div className="mt-3 grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4 pb-3">
          <div>
            <label htmlFor="msg" className="mb-1 block text-xs text-slate-500">
              Message to the resident (English, you can edit)
            </label>
            <textarea
              id="msg"
              value={message}
              onChange={(e) => {
                setMessage(e.target.value);
                setDirty(true);
              }}
              disabled={live}
              rows={3}
              className="block w-full resize-none rounded-md border border-slate-300 px-3 py-2 text-[15px] leading-snug text-slate-900 focus:border-blue-900 focus:outline-none focus:ring-2 focus:ring-blue-900/20 disabled:bg-slate-50 disabled:text-slate-600"
            />
          </div>
          <div>
            <div className="mb-1 text-xs text-slate-500">What they will hear, in {lang}</div>
            {/* The callback agent gets the English message and speaks it in their language itself
                (hc.py /callback), so this is the shape of the call, not a word-for-word preview. */}
            <ol className="min-h-[84px] space-y-0.5 rounded-md border border-dashed border-slate-300 bg-slate-50 px-3 py-2 text-[13px] leading-snug text-slate-700">
              <li>
                <span className="font-semibold text-slate-900">1.</span> CityLine&apos;s automated assistant, calling about{" "}
                <span className="font-mono">{c.case_number}</span>.
              </li>
              <li>
                <span className="font-semibold text-slate-900">2.</span> Your message, spoken in {lang}.
              </li>
              <li>
                <span className="font-semibold text-slate-900">3.</span> Asks for their reply: confirm, another time, or a question.
              </li>
            </ol>
          </div>
        </div>
      </div>

      <Footer
        state={shown}
        lang={lang}
        canCall={canCall}
        onCall={callBack}
        notSubmitted={notSubmitted}
        error={error}
        reply={c.resident_response ? RESPONSE_LABEL[c.resident_response] ?? c.resident_response : null}
        note={c.resident_response_note_en}
      />
    </section>
  );
}

// The footer is the call: one approve button, then the call's progress from the case's events.
function Footer({ state, lang, canCall, onCall, notSubmitted, error, reply, note }: {
  state: CallState | "sending";
  lang: string;
  canCall: boolean;
  onCall: () => void;
  notSubmitted: boolean;
  error: string | null;
  reply: string | null;
  note: string | null;
}) {
  const approve = (text: string) => (
    <button
      onClick={onCall}
      disabled={!canCall}
      className="min-h-12 rounded-lg bg-blue-900 px-6 text-base font-semibold text-white transition-colors hover:bg-blue-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-900 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {text}
    </button>
  );

  let body;
  if (state === "sending" || state === "calling") {
    body = (
      <>
        <span className="inline-flex min-h-12 items-center gap-2.5 rounded-lg bg-blue-900 px-5 text-base font-semibold text-white">
          <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-blue-300" />
          Calling in {lang}…
        </span>
        <span className="text-sm text-slate-600">Their phone is ringing. The reply lands here.</span>
      </>
    );
  } else if (state === "answered") {
    body = (
      <>
        <span className="inline-flex min-h-12 items-center gap-2.5 rounded-lg bg-emerald-700 px-5 text-base font-semibold text-white">
          <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-emerald-200" />
          Resident answered
        </span>
        <span className="text-[15px] text-slate-800">
          {reply ? <><b>{reply}.</b> {note}</> : "On the line now."}
        </span>
      </>
    );
  } else if (state === "done") {
    body = (
      <div className="flex flex-1 flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-[15px] font-bold text-emerald-700">Done</span>
        <span className="text-[15px] text-slate-800">
          {reply ? <><b>Resident: {reply}.</b> {note}</> : `Message delivered in ${lang}.`}
        </span>
        <span className="flex-1" />
        <span className="text-xs text-slate-500">Edit the message to call again.</span>
      </div>
    );
  } else if (state === "no_answer") {
    body = (
      <div className="flex flex-1 flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-[15px] font-bold text-amber-700">No answer</span>
        <span className="text-[15px] text-slate-800">They didn&apos;t pick up.</span>
        <span className="flex-1" />
        {approve(`Call again in ${lang}`)}
      </div>
    );
  } else {
    body = (
      <>
        {approve(`Approve and call back in ${lang}`)}
        <span className="text-sm text-slate-600">
          {notSubmitted ? "The resident hasn't submitted this case yet." : "Nothing reaches the resident until you approve."}
        </span>
      </>
    );
  }

  return (
    <div className="border-t border-slate-200 bg-slate-50 px-6 py-3">
      <div className="flex min-h-12 flex-wrap items-center gap-3">{body}</div>
      {error && <p className="mt-2 text-sm font-medium text-red-700">Couldn&apos;t call: {error}</p>}
    </div>
  );
}
