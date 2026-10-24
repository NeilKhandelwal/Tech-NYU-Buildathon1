"use client";

import { useState } from "react";
import { closeCase, reopenCase, saveAction, startCallback } from "@/lib/api";
import { callState, type CallState } from "@/lib/callback";
import { ACTIONS, RESPONSE_LABEL, messageTemplate, type Case, type CaseEvent, type CaseworkerAction } from "@/lib/types";

const label = (a: CaseworkerAction) => ACTIONS.find((x) => x.value === a)!.label;

// The one decision on this case: what to do, what to say, and the call that says it.
// Keyed by case id in the page, so switching cases starts from that case's saved values.
// open/onToggle live in the page, so the panel stays folded as the caseworker moves between cases.
export function ActionPanel({ c, events, open, onToggle }: {
  c: Case;
  events: CaseEvent[];
  open: boolean;
  onToggle: () => void;
}) {
  const suggested = c.suggested_action;
  const initial = c.caseworker_action ?? suggested ?? "schedule_inspection";
  const [action, setAction] = useState<CaseworkerAction>(initial);
  const [message, setMessage] = useState(c.caseworker_message_en ?? messageTemplate(initial, c));
  const [dirty, setDirty] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // closing/reopening is one POST; the poll brings the new status within a couple of seconds
  const [busy, setBusy] = useState<"closing" | "reopening" | null>(null);

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
      setError(`Couldn't call: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSending(false);
    }
  }

  async function setClosed(close: boolean) {
    setError(null);
    setBusy(close ? "closing" : "reopening");
    try {
      await (close ? closeCase(c.id) : reopenCase(c.id));
    } catch (e) {
      const why = e instanceof Error ? e.message : String(e);
      // an API from before /close answers 404: say what fixes it rather than just "Not Found"
      const hint = /not found|404/i.test(why) ? " (the API needs redeploying with the close endpoint)" : "";
      setError(`Couldn't ${close ? "close" : "reopen"} the case: ${why}${hint}`);
    } finally {
      // hold the label until the refetch shows the new status, so the button doesn't flicker back
      setTimeout(() => setBusy(null), 2500);
    }
  }
  const closed = c.status === "resolved";

  // a live call always holds the footer; a finished one holds it until the message changes
  const live = state === "sending" || state === "calling" || state === "answered";
  const shown: CallState | "sending" = live || !dirty ? state : "idle";
  const canCall = !notSubmitted && !!message.trim() && !live;

  return (
    <section className="border-t border-zinc-300 bg-white">
      <div className="px-6 pt-2">
        <button
          onClick={onToggle}
          aria-expanded={open}
          aria-controls="next-step-body"
          className="-mx-2 flex w-[calc(100%+1rem)] items-center gap-3 rounded-md px-2 py-1 text-left hover:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-zinc-900"
        >
          <svg viewBox="0 0 16 16" aria-hidden className={`h-4 w-4 shrink-0 text-zinc-500 transition-transform duration-200 ${open ? "" : "-rotate-90"}`}>
            <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <h3 className="shrink-0 text-xs font-semibold uppercase tracking-wider text-zinc-500">Next step</h3>
          {open ? (
            <>
              {changed && <span className="text-xs font-semibold text-amber-700">Changed from the suggestion</span>}
              {!changed && suggested && <span className="text-xs text-zinc-500">Suggested from the call</span>}
              <span className="ml-auto text-xs text-zinc-500">Hide</span>
            </>
          ) : (
            // folded: the decision and the start of the message, so the case view gets the room
            <>
              <span className="shrink-0 text-[15px] font-bold text-zinc-900">{label(action)}</span>
              <span className="min-w-0 truncate text-sm text-zinc-500">{message}</span>
              <span className="ml-auto shrink-0 text-xs text-zinc-500">Edit</span>
            </>
          )}
        </button>
      </div>
      <div id="next-step-body" hidden={!open} className="px-6">
        <div className="mt-1 flex flex-wrap items-center gap-x-6 gap-y-2">
          <h2 className="text-[26px] font-bold leading-tight tracking-tight text-zinc-900">{label(action)}</h2>
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Choose a different next step">
            <span className="mr-1 text-xs text-zinc-500">Or instead:</span>
            {ACTIONS.filter((a) => a.value !== action).map((a) => (
              <button
                key={a.value}
                onClick={() => pick(a.value)}
                disabled={live}
                className="min-h-9 rounded-full border border-zinc-300 bg-white px-3 text-[13px] text-zinc-700 transition-colors hover:border-zinc-900 hover:text-zinc-950 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {a.label}
              </button>
            ))}
            {changed && (
              <button
                onClick={() => pick(suggested!)}
                disabled={live}
                className="min-h-9 rounded-full border border-zinc-900 bg-white px-3 text-[13px] font-semibold text-zinc-900 disabled:opacity-40"
              >
                Back to suggestion
              </button>
            )}
          </div>
        </div>

        <div className="mt-3 grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4 pb-3">
          <div>
            <label htmlFor="msg" className="mb-1 block text-xs text-zinc-500">
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
              className="block w-full resize-none rounded-md border border-zinc-300 px-3 py-2 text-[15px] leading-snug text-zinc-900 focus:border-zinc-900 focus:outline-none focus:ring-2 focus:ring-yellow-400/50 disabled:bg-zinc-50 disabled:text-zinc-600"
            />
          </div>
          <div>
            <div className="mb-1 text-xs text-zinc-500">What they will hear, in {lang}</div>
            {/* The callback agent gets the English message and speaks it in their language itself
                (hc.py /callback), so this is the shape of the call, not a word-for-word preview. */}
            <ol className="min-h-[84px] space-y-0.5 rounded-md border border-dashed border-zinc-300 bg-zinc-50 px-3 py-2 text-[13px] leading-snug text-zinc-700">
              <li>
                <span className="font-semibold text-zinc-900">1.</span> CityLine&apos;s automated assistant, calling about{" "}
                <span className="font-mono">{c.case_number}</span>.
              </li>
              <li>
                <span className="font-semibold text-zinc-900">2.</span> Your message, spoken in {lang}.
              </li>
              <li>
                <span className="font-semibold text-zinc-900">3.</span> Asks for their reply: confirm, another time, or a question.
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
        closed={closed}
        busy={busy}
        onClose={() => setClosed(true)}
        onReopen={() => setClosed(false)}
      />
    </section>
  );
}

// The footer is the call: one approve button, then the call's progress from the case's events.
function Footer({ state, lang, canCall, onCall, notSubmitted, error, reply, note, closed, busy, onClose, onReopen }: {
  state: CallState | "sending";
  lang: string;
  canCall: boolean;
  onCall: () => void;
  notSubmitted: boolean;
  error: string | null;
  reply: string | null;
  note: string | null;
  closed: boolean;
  busy: "closing" | "reopening" | null;
  onClose: () => void;
  onReopen: () => void;
}) {
  const approve = (text: string) => (
    <button
      onClick={onCall}
      disabled={!canCall}
      className="min-h-12 rounded-lg bg-yellow-400 px-6 text-base font-semibold text-zinc-950 transition-colors hover:bg-yellow-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {text}
    </button>
  );

  const live = state === "sending" || state === "calling" || state === "answered";
  // Close sits apart from the call, at the far right: finishing the case is not a message
  const closeButton = !live && (
    <button
      onClick={onClose}
      disabled={busy !== null}
      className="ml-auto flex min-h-10 items-center gap-2 rounded-lg border border-zinc-300 bg-white px-4 text-sm font-semibold text-zinc-800 transition-colors hover:border-zinc-900 hover:text-zinc-950 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900 disabled:opacity-50"
    >
      <svg viewBox="0 0 16 16" aria-hidden className="h-4 w-4">
        <path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {busy === "closing" ? "Closing…" : "Close case"}
    </button>
  );

  let body;
  if (closed) {
    body = (
      <div className="flex flex-1 flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-[15px] font-bold text-zinc-900">Case closed</span>
        <span className="text-[15px] text-zinc-600">Off the open queue. The record and its timeline stay.</span>
        <button
          onClick={onReopen}
          disabled={busy !== null}
          className="ml-auto min-h-10 rounded-lg border border-zinc-900 bg-white px-4 text-sm font-semibold text-zinc-900 hover:bg-zinc-100 disabled:opacity-50"
        >
          {busy === "reopening" ? "Reopening…" : "Reopen"}
        </button>
      </div>
    );
  } else if (state === "sending" || state === "calling") {
    body = (
      <>
        <span className="inline-flex min-h-12 items-center gap-2.5 rounded-lg bg-zinc-950 px-5 text-base font-semibold text-white">
          <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-yellow-400" />
          Calling in {lang}…
        </span>
        <span className="text-sm text-zinc-600">Their phone is ringing. The reply lands here.</span>
      </>
    );
  } else if (state === "answered") {
    body = (
      <>
        <span className="inline-flex min-h-12 items-center gap-2.5 rounded-lg bg-emerald-700 px-5 text-base font-semibold text-white">
          <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-emerald-200" />
          Resident answered
        </span>
        <span className="text-[15px] text-zinc-800">
          {reply ? <><b>{reply}.</b> {note}</> : "On the line now."}
        </span>
      </>
    );
  } else if (state === "done") {
    body = (
      <div className="flex flex-1 flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-[15px] font-bold text-emerald-700">Done</span>
        <span className="text-[15px] text-zinc-800">
          {reply ? <><b>Resident: {reply}.</b> {note}</> : `Message delivered in ${lang}.`}
        </span>
        <span className="text-xs text-zinc-500">Edit the message to call again.</span>
        {closeButton}
      </div>
    );
  } else if (state === "no_answer") {
    body = (
      <div className="flex flex-1 flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-[15px] font-bold text-amber-700">No answer</span>
        <span className="text-[15px] text-zinc-800">They didn&apos;t pick up.</span>
        {approve(`Call again in ${lang}`)}
        {closeButton}
      </div>
    );
  } else {
    body = (
      <>
        {approve(`Approve and call back in ${lang}`)}
        <span className="text-sm text-zinc-600">
          {notSubmitted ? "The resident hasn't submitted this case yet." : "Nothing reaches the resident until you approve."}
        </span>
        {closeButton}
      </>
    );
  }

  return (
    <div className="border-t border-zinc-200 bg-zinc-50 px-6 py-3">
      <div className="flex min-h-12 flex-wrap items-center gap-3">{body}</div>
      {error && <p className="mt-2 text-sm font-medium text-red-700">{error}</p>}
    </div>
  );
}
