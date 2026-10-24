"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useState } from "react";
import {
  Case,
  CaseEvent,
  CallWithTranscript,
  ISSUE_TYPES,
  STATUSES,
  STATUS_COLOR,
  Status,
  Tracked,
  addNote,
  duration,
  flash,
  getCase,
  getCaseCalls,
  getCaseEvents,
  humanize,
  patchCase,
  track,
  untracked,
  useLiveRefresh,
  useNow,
} from "@/lib/api";
import Transcript from "@/components/Transcript";

export default function CaseDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [c, setCase] = useState<Tracked<Case>>(untracked);
  const [calls, setCalls] = useState<CallWithTranscript[]>([]);
  const [events, setEvents] = useState<CaseEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState(""); // the note being typed; notes themselves are events
  const [noteError, setNoteError] = useState<string | null>(null);

  const load = useCallback(() => {
    getCase(id)
      .then((data) => {
        setCase((prev) => track(prev, data));
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
    getCaseCalls(id)
      .then(setCalls)
      .catch(() => setCalls([]));
    getCaseEvents(id)
      .then(setEvents)
      .catch(() => setEvents([]));
  }, [id]);

  useLiveRefresh(load);
  useNow(); // durations tick every second, not only when data arrives

  const update = (body: Partial<Pick<Case, "status" | "issue_type" | "description">>) =>
    patchCase(id, body)
      .then((data) => {
        setCase((prev) => track(prev, data));
        setError(null);
        return data;
      })
      .catch((e: Error) => setError(e.message));

  const submitNote = async () => {
    const text = note.trim();
    if (!text) return;
    try {
      await addNote(id, text);
      setNote(""); // it is stored; the refetch below is what renders it
      setNoteError(null);
      load();
    } catch (e) {
      setNoteError((e as Error).message); // keep what was typed so it can be retried
    }
  };

  const noteEvents = events.filter((e) => e.field === "note"); // events, not case.notes: they carry ts + source

  // source says which system wrote the row, actor which person; a voice call names nobody
  const who = (e: CaseEvent) => (e.actor ? `${e.source} · ${e.actor}` : e.source);

  // status/issue_type are enums worth prettifying; description and notes are free text
  const val = (e: CaseEvent, v: string | null) =>
    !v ? "—" : e.field === "status" || e.field === "issue_type" ? humanize(v) : v;
  const cs = c.data;
  // a call waiting on staff (needs_person) is still in progress, so it reads as the live one
  const liveCall = calls.find((k) => k.status !== "ended");
  const ended = calls.filter((k) => k.status === "ended");
  const card = "rounded-lg border border-slate-200 bg-white shadow-sm p-4";
  const h2 = "text-xs uppercase tracking-wide text-slate-500 mb-3";

  return (
    <main className="p-6 max-w-6xl mx-auto w-full">
      <Link href="/" className="text-sm text-blue-700 hover:underline">
        &larr; All cases
      </Link>
      <div className="flex items-center gap-3 mt-2 mb-4">
        <h1 className="text-xl font-semibold">Case {id}</h1>
        {cs && (
          <span className={`px-2 py-0.5 rounded-full text-xs ring-1 ${STATUS_COLOR[cs.status]} ${flash(c, "status")}`}>
            {humanize(cs.status)}
          </span>
        )}
        {liveCall && (
          <Link
            href={`/calls/${liveCall.id}`}
            className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs bg-red-50 text-red-700 ring-1 ring-red-200 hover:bg-red-100"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" /> On call · {liveCall.id}
          </Link>
        )}
      </div>
      {error && <p className="text-red-700 mb-4 text-sm">{error}</p>}

      {cs && (
        <div className="grid md:grid-cols-3 gap-4">
          <div className="md:col-span-2 space-y-4">
            <section className={card}>
              <h2 className={h2}>Details</h2>
              <dl className="grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
                <dt className="text-slate-500">Resident</dt>
                <dd className={flash(c, "name")}>{cs.name}</dd>
                <dt className="text-slate-500">Phone</dt>
                <dd className={`font-mono text-xs self-center ${flash(c, "phone")}`}>{cs.phone}</dd>
                <dt className="text-slate-500">Issue</dt>
                <dd className={flash(c, "issue_type")}>{cs.issue_type ? humanize(cs.issue_type) : "—"}</dd>
                <dt className="text-slate-500">Description</dt>
                <dd className={flash(c, "description")}>{cs.description}</dd>
              </dl>
            </section>

            <section className={card}>
              <h2 className={h2}>Notes ({noteEvents.length})</h2>
              {noteEvents.length === 0 && <p className="text-slate-500 text-sm mb-3">No notes yet.</p>}
              <ol className="space-y-2 mb-3">
                {noteEvents.map((e) => (
                  <li key={e.id} className="border-l-2 border-slate-200 pl-3">
                    <div className="flex items-center gap-2 text-xs text-slate-500 mb-0.5">
                      <span className="font-mono">{new Date(e.ts).toLocaleString()}</span>
                      <span
                        className={`px-1.5 rounded-full ring-1 ${
                          e.source === "voice" ? "bg-violet-50 text-violet-700 ring-violet-200" : "bg-slate-100 text-slate-600 ring-slate-200"
                        }`}
                      >
                        {who(e)}
                      </span>
                    </div>
                    <p className="text-sm whitespace-pre-wrap break-words">{e.new_value}</p>
                  </li>
                ))}
              </ol>
              {/* append-only: a note is a row of its own, so there is nothing to edit or save */}
              <div className="flex items-center gap-2">
                <input
                  id="note"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && submitNote()}
                  placeholder="Add a note — the agent's add_note tool appends here too."
                  className="flex-1 min-w-0 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                />
                <button
                  onClick={submitNote}
                  disabled={!note.trim()}
                  className="px-3 py-1.5 rounded-md bg-slate-900 text-white text-sm disabled:opacity-40 shrink-0"
                >
                  Add note
                </button>
              </div>
              {noteError && <p className="text-red-700 text-xs mt-2">{noteError}</p>}
            </section>

            <section className={card}>
              <h2 className={h2}>Calls ({calls.length})</h2>
              {calls.length === 0 && <p className="text-slate-500 text-sm">No calls linked to this case.</p>}
              {/* the call happening right now is the only one worth reading in full; the rest fold away */}
              {liveCall && (
                <div className="mb-4">
                  <div className="flex items-center gap-2 text-sm mb-1.5">
                    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs bg-red-50 text-red-700 ring-1 ring-red-200">
                      <span className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" /> LIVE
                    </span>
                    <Link href={`/calls/${liveCall.id}`} className="font-medium text-blue-700 hover:underline">
                      {liveCall.id}
                    </Link>
                    <span className="text-xs text-slate-500">
                      started {new Date(liveCall.started_at).toLocaleTimeString()} ·{" "}
                      <span className="font-mono">{duration(liveCall.started_at, null)}</span>
                    </span>
                  </div>
                  <Transcript lines={liveCall.transcript} className="max-h-[60vh] min-h-64" active />
                </div>
              )}
              <div className="space-y-2">
                {ended.map((call) => (
                  <details key={call.id} className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
                    <summary className="cursor-pointer text-sm text-slate-700">
                      {call.id} · ended · <span className="font-mono">{duration(call.started_at, call.ended_at)}</span> ·{" "}
                      {call.transcript.length} lines
                    </summary>
                    <div className="pt-2">
                      {call.summary && <p className="mb-1.5 text-sm italic text-slate-600">{call.summary}</p>}
                      <Link href={`/calls/${call.id}`} className="text-xs text-blue-700 hover:underline">
                        Open call
                      </Link>
                      <Transcript lines={call.transcript} className="max-h-72 mt-1.5" />
                    </div>
                  </details>
                ))}
              </div>
            </section>
          </div>

          <aside className={`${card} text-sm h-fit space-y-4`}>
            <div>
              <h2 className={h2}>Triage</h2>
              <label className="block text-slate-500 text-xs mb-1" htmlFor="status">Status</label>
              <select
                id="status"
                value={cs.status}
                onChange={(e) => update({ status: e.target.value as Status })}
                className="w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 mb-3"
              >
                {STATUSES.map((s) => (
                  <option key={s} value={s}>{humanize(s)}</option>
                ))}
              </select>
              <label className="block text-slate-500 text-xs mb-1" htmlFor="issue">Issue type</label>
              <select
                id="issue"
                value={cs.issue_type ?? ""}
                onChange={(e) => update({ issue_type: e.target.value })}
                className="w-full rounded-md border border-slate-300 bg-white px-2 py-1.5"
              >
                {/* only shown until the agent or staff classifies; not selectable back */}
                <option value="" disabled>Not classified</option>
                {ISSUE_TYPES.map((s) => (
                  <option key={s} value={s}>{humanize(s)}</option>
                ))}
              </select>
            </div>
            <dl className="space-y-1.5 text-xs border-t border-slate-100 pt-3">
              <div className="flex justify-between"><dt className="text-slate-500">Created</dt><dd>{new Date(cs.created_at).toLocaleString()}</dd></div>
              <div className="flex justify-between"><dt className="text-slate-500">Updated</dt><dd>{new Date(cs.updated_at).toLocaleString()}</dd></div>
              <div className="flex justify-between"><dt className="text-slate-500">Calls</dt><dd>{calls.length}</dd></div>
            </dl>
            <div className="border-t border-slate-100 pt-3">
              <h2 className={h2}>History</h2>
              {events.length === 0 && <p className="text-slate-500 text-xs">No changes recorded.</p>}
              <ol className="space-y-2 text-xs">
                {[...events].reverse().map((e) => (
                  <li key={e.id} className="flex gap-2">
                    <span className="text-slate-400 font-mono shrink-0">{new Date(e.ts).toLocaleTimeString()}</span>
                    <span
                      className={`px-1.5 rounded-full shrink-0 ring-1 ${
                        e.source === "voice" ? "bg-violet-50 text-violet-700 ring-violet-200" : "bg-slate-100 text-slate-600 ring-slate-200"
                      }`}
                    >
                      {who(e)}
                    </span>
                    <span className="min-w-0 break-words">
                      {e.field === "created" ? (
                        "Case created"
                      ) : e.field === "call_linked" ? (
                        <>Linked {e.new_value}</>
                      ) : e.field === "looked_up" ? (
                        <>Looked up by {e.source}</>
                      ) : e.field === "note" ? (
                        // a note has no old value; a strikethrough diff would be nonsense
                        <><span className="text-slate-500">Note:</span> {e.new_value}</>
                      ) : (
                        <>
                          <span className="text-slate-500">{humanize(e.field)}:</span>{" "}
                          <span className="text-slate-400 line-through">{val(e, e.old_value)}</span> → {val(e, e.new_value)}
                        </>
                      )}
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          </aside>
        </div>
      )}
    </main>
  );
}
