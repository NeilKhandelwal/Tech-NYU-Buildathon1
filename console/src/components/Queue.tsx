"use client";

import { useState } from "react";
import type { Case } from "@/lib/types";
import { ago, isOpen, queueOrder } from "@/lib/format";
import { LanguageBadge, PriorityBadge, StatusText, ViolationBadge } from "./ui";

const FILTERS = {
  all: { label: "All", keep: () => true },
  open: { label: "Open", keep: isOpen },
  urgent: { label: "Urgent", keep: (c: Case) => isOpen(c) && c.priority === "urgent" },
  violation: { label: "Violations", keep: (c: Case) => isOpen(c) && c.is_violation === true },
  resolved: { label: "Resolved", keep: (c: Case) => c.status === "resolved" },
} satisfies Record<string, { label: string; keep: (c: Case) => boolean }>;
type Filter = keyof typeof FILTERS;

export function Queue({ cases, selectedId, onSelect, fresh, now }: {
  cases: Case[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  fresh: Set<string>;
  now: number;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const shown = cases.filter(FILTERS[filter].keep).sort(queueOrder);

  return (
    <aside className="flex w-[360px] shrink-0 flex-col border-r border-slate-200 bg-white">
      <div className="flex items-center justify-between px-4 pt-4 pb-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500">Queue</h2>
        <span className="text-xs tabular-nums text-slate-400">{shown.length} cases</span>
      </div>
      <div className="flex flex-wrap gap-1 px-4 pb-3">
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
      </div>
      <ol className="flex-1 overflow-y-auto border-t border-slate-100">
        {shown.map((c) => (
          <li key={c.id}>
            <button
              onClick={() => onSelect(c.id)}
              className={`block w-full border-b border-l-4 border-b-slate-100 px-4 py-3 text-left transition-colors ${
                c.id === selectedId ? "border-l-blue-900 bg-blue-50/60" : "border-l-transparent hover:bg-slate-50"
              } ${fresh.has(c.id) ? "animate-arrive" : ""}`}
            >
              <div className="flex items-center gap-2">
                <span className="font-mono text-[15px] font-semibold text-slate-900">{c.case_number}</span>
                {fresh.has(c.id) && (
                  <span className="rounded bg-blue-900 px-1.5 py-0.5 text-[10px] font-bold uppercase text-white">New</span>
                )}
                <span className="ml-auto text-xs tabular-nums text-slate-400">{ago(c.created_at, now)}</span>
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-slate-600">
                <LanguageBadge label={c.language_label ?? c.language} />
                <span>{c.borough ?? "—"}</span>
                <span className="text-slate-300">·</span>
                <StatusText value={c.status} />
              </div>
              <div className="mt-1.5 flex items-center gap-1.5">
                <ViolationBadge value={c.is_violation} />
                <PriorityBadge value={c.priority} />
              </div>
            </button>
          </li>
        ))}
        {shown.length === 0 && <li className="px-4 py-8 text-center text-sm text-slate-400">No cases here.</li>}
      </ol>
    </aside>
  );
}
