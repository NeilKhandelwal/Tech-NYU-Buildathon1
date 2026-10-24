"use client";

import { useState } from "react";
import { AGENT_LABEL, elapsed, type Call } from "@/lib/calls";
import { COMPLAINT_LABEL, complaintOf, isHeat, type Case, type ComplaintType } from "@/lib/types";
import { ago, borough, isOpen, nyClock, queueOrder } from "@/lib/format";
import { IssueBadge, LanguageBadge, PriorityBadge, StatusText, ViolationBadge } from "./ui";

const FILTERS = {
  all: { label: "All", keep: () => true },
  open: { label: "Open", keep: isOpen },
  urgent: { label: "Urgent", keep: (c: Case) => isOpen(c) && c.priority === "urgent" },
  violation: { label: "Violations", keep: (c: Case) => isOpen(c) && c.is_violation === true },
  resolved: { label: "Resolved", keep: (c: Case) => c.status === "resolved" },
} satisfies Record<string, { label: string; keep: (c: Case) => boolean }>;
type Filter = keyof typeof FILTERS;

export type Selection = { kind: "case" | "call"; id: string } | null;

export function Queue({ cases, calls, selection, onSelectCase, onSelectCall, fresh, now }: {
  cases: Case[];
  calls: Call[];
  selection: Selection;
  onSelectCase: (id: string) => void;
  onSelectCall: (call: Call) => void;
  fresh: Set<string>;
  now: number;
}) {
  const [tab, setTab] = useState<"cases" | "calls">("cases");
  // folded to a thin rail so the case gets the width; the list keeps its state underneath
  const [folded, setFolded] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const [issue, setIssue] = useState<ComplaintType | "all">("all");
  const shown = cases
    .filter(FILTERS[filter].keep)
    .filter((c) => issue === "all" || complaintOf(c) === issue)
    .sort(queueOrder);
  const live = calls.filter((c) => c.live);
  const byId = new Map(cases.map((c) => [c.id, c]));

  return (
    <aside
      className={`relative flex shrink-0 overflow-hidden border-r border-zinc-200 bg-white transition-[width] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] motion-reduce:transition-none ${
        folded ? "w-14" : "w-[360px]"
      }`}
    >
      {folded && (
        <button
          onClick={() => setFolded(false)}
          aria-label="Show the case list"
          aria-expanded={false}
          className="absolute inset-0 z-10 flex flex-col items-center gap-4 pt-3 text-zinc-500 hover:bg-zinc-50 hover:text-zinc-900 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-zinc-900"
        >
          <svg viewBox="0 0 16 16" aria-hidden className="h-4 w-4">
            <path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="text-lg font-semibold tabular-nums text-zinc-900">{cases.filter(isOpen).length}</span>
          <span className="-mt-3 text-[10px] font-semibold uppercase tracking-wider">Open</span>
          {live.length > 0 && (
            <span className="flex flex-col items-center text-red-700">
              <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-red-600" />
              <span className="mt-1 text-[10px] font-semibold uppercase">{live.length} live</span>
            </span>
          )}
        </button>
      )}
      <div className={`flex w-[360px] shrink-0 flex-col ${folded ? "invisible" : ""}`}>
      {live.length > 0 && (
        <div className="border-b border-red-100 bg-red-50/60">
          <h2 className="flex items-center gap-2 px-4 pt-3 pb-1 text-xs font-semibold uppercase tracking-wider text-red-700">
            <span className="h-2 w-2 animate-pulse rounded-full bg-red-600" /> On the line now
          </h2>
          <ol>
            {live.map((call) => (
              <CallRow key={call.conversationId} call={call} c={call.caseId ? byId.get(call.caseId) : undefined}
                selected={isSelected(selection, call)} onClick={() => onSelectCall(call)} now={now} />
            ))}
          </ol>
        </div>
      )}

      <div className="flex items-center gap-4 border-b border-zinc-100 px-4 pt-3">
        {(["cases", "calls"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`-mb-px border-b-2 pb-2 text-xs font-semibold uppercase tracking-wider ${
              tab === t ? "border-yellow-400 text-zinc-900" : "border-transparent text-zinc-400 hover:text-zinc-600"
            }`}
          >
            {t === "cases" ? `Cases · ${shown.length}` : `Calls · ${calls.length}`}
          </button>
        ))}
        <button
          onClick={() => setFolded(true)}
          aria-label="Hide the case list"
          aria-expanded={true}
          className="-mt-1 mb-1 ml-auto flex h-7 w-7 items-center justify-center rounded text-zinc-400 hover:bg-zinc-100 hover:text-zinc-900"
        >
          <svg viewBox="0 0 16 16" aria-hidden className="h-4 w-4">
            <path d="M10 4L6 8l4 4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>

      {tab === "cases" ? (
        <>
          <div className="flex flex-wrap items-center gap-1 px-4 py-3">
            {(Object.keys(FILTERS) as Filter[]).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`rounded px-2 py-1 text-xs font-medium ${
                  filter === f ? "bg-zinc-900 text-white" : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200"
                }`}
              >
                {FILTERS[f].label}
              </button>
            ))}
            <select
              value={issue}
              onChange={(e) => setIssue(e.target.value as ComplaintType | "all")}
              className="mt-1 w-full rounded border border-zinc-200 bg-white px-2 py-1 text-xs text-zinc-700"
              aria-label="Problem type"
            >
              <option value="all">All problem types</option>
              {(Object.keys(COMPLAINT_LABEL) as ComplaintType[]).map((t) => (
                <option key={t} value={t}>{COMPLAINT_LABEL[t]}</option>
              ))}
            </select>
          </div>
          <ol className="flex-1 overflow-y-auto border-t border-zinc-100">
            {shown.map((c) => (
              <li key={c.id}>
                <button
                  onClick={() => onSelectCase(c.id)}
                  className={`block w-full border-b border-l-4 border-b-zinc-100 px-4 py-3 text-left transition-colors ${
                    selection?.kind === "case" && selection.id === c.id
                      ? "border-l-yellow-400 bg-yellow-50"
                      : "border-l-transparent hover:bg-zinc-50"
                  } ${fresh.has(c.id) ? "animate-arrive" : ""}`}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[15px] font-semibold text-zinc-900">{c.case_number}</span>
                    {fresh.has(c.id) && (
                      <span className="rounded bg-yellow-400 px-1.5 py-0.5 text-[10px] font-bold uppercase text-zinc-950">New</span>
                    )}
                    {live.some((call) => call.caseId === c.id) && (
                      <span className="h-2 w-2 animate-pulse rounded-full bg-red-600" title="On the line now" />
                    )}
                    <span className="ml-auto text-xs tabular-nums text-zinc-400">{ago(c.created_at, now)}</span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-zinc-600">
                    <IssueBadge c={c} />
                    <LanguageBadge label={c.language_label ?? c.language} />
                    <span>{borough(c) ?? "—"}</span>
                    <span className="text-zinc-300">·</span>
                    <StatusText value={c.status} />
                  </div>
                  <div className="mt-1.5 flex items-center gap-1.5">
                    {isHeat(c) && <ViolationBadge value={c.is_violation} />}
                    <PriorityBadge value={c.priority} />
                  </div>
                </button>
              </li>
            ))}
            {shown.length === 0 && <li className="px-4 py-8 text-center text-sm text-zinc-400">No cases here.</li>}
          </ol>
        </>
      ) : (
        <ol className="flex-1 overflow-y-auto">
          {calls.map((call) => (
            <CallRow key={call.conversationId} call={call} c={call.caseId ? byId.get(call.caseId) : undefined}
              selected={isSelected(selection, call)} onClick={() => onSelectCall(call)} now={now} />
          ))}
          {calls.length === 0 && (
            <li className="px-4 py-8 text-center text-sm text-zinc-400">No calls in the last 3 hours.</li>
          )}
        </ol>
      )}
      </div>
    </aside>
  );
}

function isSelected(selection: Selection, call: Call) {
  if (!selection) return false;
  return selection.kind === "call" ? selection.id === call.conversationId : selection.id === call.caseId;
}

function CallRow({ call, c, selected, onClick, now }: {
  call: Call;
  c?: Case;
  selected: boolean;
  onClick: () => void;
  now: number;
}) {
  const last = call.lines.at(-1);
  return (
    <li>
      <button
        onClick={onClick}
        className={`block w-full border-b border-l-4 border-b-zinc-100 px-4 py-2.5 text-left ${
          selected ? "border-l-yellow-400 bg-yellow-50" : "border-l-transparent hover:bg-zinc-50"
        }`}
      >
        <div className="flex items-center gap-2 text-sm">
          {call.live && <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-red-600" />}
          <span className="font-semibold text-zinc-900">{AGENT_LABEL[call.agent]}</span>
          <span className="font-mono text-xs text-zinc-500">
            {c ? c.case_number : call.caller ? `…${call.caller.slice(-4)}` : "no case yet"}
          </span>
          <span className="ml-auto text-xs tabular-nums text-zinc-400">
            {call.live ? elapsed(call.startedAt, now) : nyClock(call.startedAt)}
          </span>
        </div>
        {last && (
          <p className="mt-0.5 truncate text-xs text-zinc-500">
            <span className="font-medium">{last.who === "resident" ? "Resident" : "Agent"}:</span>{" "}
            {last.english ?? last.original}
          </p>
        )}
      </button>
    </li>
  );
}
