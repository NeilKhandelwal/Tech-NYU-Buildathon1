"use client";

import { useState } from "react";
import { AGENT_LABEL, elapsed, type Call } from "@/lib/calls";
import { ISSUE_LABEL, issueOf, isHeat, type Case, type IssueType } from "@/lib/types";
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
  const [filter, setFilter] = useState<Filter>("all");
  const [issue, setIssue] = useState<IssueType | "all">("all");
  const shown = cases
    .filter(FILTERS[filter].keep)
    .filter((c) => issue === "all" || issueOf(c) === issue)
    .sort(queueOrder);
  const live = calls.filter((c) => c.live);
  const byId = new Map(cases.map((c) => [c.id, c]));

  return (
    <aside className="flex w-[360px] shrink-0 flex-col border-r border-slate-200 bg-white">
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

      <div className="flex items-center gap-4 border-b border-slate-100 px-4 pt-3">
        {(["cases", "calls"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`-mb-px border-b-2 pb-2 text-xs font-semibold uppercase tracking-wider ${
              tab === t ? "border-blue-900 text-blue-900" : "border-transparent text-slate-400 hover:text-slate-600"
            }`}
          >
            {t === "cases" ? `Cases · ${shown.length}` : `Calls · ${calls.length}`}
          </button>
        ))}
      </div>

      {tab === "cases" ? (
        <>
          <div className="flex flex-wrap items-center gap-1 px-4 py-3">
            {(Object.keys(FILTERS) as Filter[]).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`rounded px-2 py-1 text-xs font-medium ${
                  filter === f ? "bg-blue-900 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                }`}
              >
                {FILTERS[f].label}
              </button>
            ))}
            <select
              value={issue}
              onChange={(e) => setIssue(e.target.value as IssueType | "all")}
              className="mt-1 w-full rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700"
              aria-label="Problem type"
            >
              <option value="all">All problem types</option>
              {(Object.keys(ISSUE_LABEL) as IssueType[]).map((t) => (
                <option key={t} value={t}>{ISSUE_LABEL[t]}</option>
              ))}
            </select>
          </div>
          <ol className="flex-1 overflow-y-auto border-t border-slate-100">
            {shown.map((c) => (
              <li key={c.id}>
                <button
                  onClick={() => onSelectCase(c.id)}
                  className={`block w-full border-b border-l-4 border-b-slate-100 px-4 py-3 text-left transition-colors ${
                    selection?.kind === "case" && selection.id === c.id
                      ? "border-l-blue-900 bg-blue-50/60"
                      : "border-l-transparent hover:bg-slate-50"
                  } ${fresh.has(c.id) ? "animate-arrive" : ""}`}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[15px] font-semibold text-slate-900">{c.case_number}</span>
                    {fresh.has(c.id) && (
                      <span className="rounded bg-blue-900 px-1.5 py-0.5 text-[10px] font-bold uppercase text-white">New</span>
                    )}
                    {live.some((call) => call.caseId === c.id) && (
                      <span className="h-2 w-2 animate-pulse rounded-full bg-red-600" title="On the line now" />
                    )}
                    <span className="ml-auto text-xs tabular-nums text-slate-400">{ago(c.created_at, now)}</span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-slate-600">
                    <IssueBadge c={c} />
                    <LanguageBadge label={c.language_label ?? c.language} />
                    <span>{borough(c) ?? "—"}</span>
                    <span className="text-slate-300">·</span>
                    <StatusText value={c.status} />
                  </div>
                  <div className="mt-1.5 flex items-center gap-1.5">
                    {isHeat(c) && <ViolationBadge value={c.is_violation} />}
                    <PriorityBadge value={c.priority} />
                  </div>
                </button>
              </li>
            ))}
            {shown.length === 0 && <li className="px-4 py-8 text-center text-sm text-slate-400">No cases here.</li>}
          </ol>
        </>
      ) : (
        <ol className="flex-1 overflow-y-auto">
          {calls.map((call) => (
            <CallRow key={call.conversationId} call={call} c={call.caseId ? byId.get(call.caseId) : undefined}
              selected={isSelected(selection, call)} onClick={() => onSelectCall(call)} now={now} />
          ))}
          {calls.length === 0 && (
            <li className="px-4 py-8 text-center text-sm text-slate-400">No calls in the last 3 hours.</li>
          )}
        </ol>
      )}
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
        className={`block w-full border-b border-l-4 border-b-slate-100 px-4 py-2.5 text-left ${
          selected ? "border-l-blue-900 bg-blue-50/60" : "border-l-transparent hover:bg-slate-50"
        }`}
      >
        <div className="flex items-center gap-2 text-sm">
          {call.live && <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-red-600" />}
          <span className="font-semibold text-slate-900">{AGENT_LABEL[call.agent]}</span>
          <span className="font-mono text-xs text-slate-500">
            {c ? c.case_number : call.caller ? `…${call.caller.slice(-4)}` : "no case yet"}
          </span>
          <span className="ml-auto text-xs tabular-nums text-slate-400">
            {call.live ? elapsed(call.startedAt, now) : nyClock(call.startedAt)}
          </span>
        </div>
        {last && (
          <p className="mt-0.5 truncate text-xs text-slate-500">
            <span className="font-medium">{last.who === "resident" ? "Resident" : "Agent"}:</span>{" "}
            {last.english ?? last.original}
          </p>
        )}
      </button>
    </li>
  );
}
