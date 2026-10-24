"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import {
  Case,
  CallWithTranscript,
  ISSUE_TYPES,
  STATUSES,
  STATUS_COLOR,
  Stats,
  Status,
  TrackedList,
  ago,
  caseIdsOf,
  duration,
  getCall,
  getStats,
  humanize,
  listCalls,
  listCases,
  trackList,
  untrackedList,
  useLiveRefresh,
  useNow,
} from "@/lib/api";

// The containment window. null = all time; 0 = today, from local midnight.
const WINDOWS: { label: string; days: number | null }[] = [
  { label: "Today", days: 0 },
  { label: "7 days", days: 7 },
  { label: "30 days", days: 30 },
  { label: "All", days: null },
];

function windowSince(days: number | null) {
  if (days === null) return undefined;
  const d = new Date();
  if (days === 0) d.setHours(0, 0, 0, 0);
  else d.setDate(d.getDate() - days);
  // seconds, no millis: the backend compares these as strings against its own "…Z" stamps
  return d.toISOString().slice(0, 19) + "Z";
}

export default function CasesPage() {
  const [cases, setCases] = useState<TrackedList<Case>>(untrackedList);
  const [live, setLive] = useState<CallWithTranscript[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [refreshedAt, setRefreshedAt] = useState("");
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Status | "all">("all");
  const [issue, setIssue] = useState("all");
  const [days, setDays] = useState<number | null>(7);
  const [stats, setStats] = useState<Stats | null>(null);

  const load = useCallback(() => {
    listCases()
      .then((data) => {
        setCases((prev) => trackList(prev, data));
        setError(null);
        setRefreshedAt(new Date().toLocaleTimeString());
      })
      .catch((e: Error) => setError(e.message));
    // Live calls need their transcript for the "last line" preview; /calls has none.
    // needs_person calls are still on the line, so they belong here — and go first.
    listCalls()
      .then((calls) => Promise.all(calls.filter((c) => c.status !== "ended").map((c) => getCall(c.id))))
      .then((calls) =>
        setLive(calls.sort((a, b) => Number(b.status === "needs_person") - Number(a.status === "needs_person"))),
      )
      .catch(() => setLive([]));
    getStats(windowSince(days))
      .then(setStats)
      .catch(() => setStats(null));
    // days is a dep so changing the window refetches immediately: useLiveRefresh runs
    // refetch() whenever its identity changes.
  }, [days]);

  useLiveRefresh(load);
  useNow(); // durations tick every second, not only when data arrives

  const onCall = new Set(live.flatMap(caseIdsOf)); // a call can be working several cases
  const rows = cases.data;
  const count = (s: Status) => rows.filter((c) => c.status === s).length;
  const needle = q.trim().toLowerCase();
  const shown = rows.filter(
    (c) =>
      (filter === "all" || c.status === filter) &&
      (issue === "all" || c.issue_type === issue) &&
      (!needle || [c.id, c.name, c.phone, c.issue_type, c.description].join(" ").toLowerCase().includes(needle)),
  );

  const tile = (label: string, value: number, key: Status | "all", accent = "") => (
    <button
      onClick={() => setFilter(filter === key ? "all" : key)}
      className={`text-left rounded-lg border bg-white px-4 py-3 shadow-sm transition ${
        filter === key ? "border-slate-900 ring-1 ring-slate-900" : "border-slate-200 hover:border-slate-400"
      }`}
    >
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`text-2xl font-semibold ${accent}`}>{value}</div>
    </button>
  );

  return (
    <main className="p-6 max-w-6xl mx-auto w-full">
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <h1 className="text-xl font-semibold">Cases</h1>
          <p className="text-sm text-slate-500">Service requests taken by the voice agent</p>
        </div>
        <span className="text-xs text-slate-500">{refreshedAt ? `updated ${refreshedAt}` : "loading…"}</span>
      </div>
      {error && <p className="text-red-700 mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm">{error}</p>}

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
        <div className="col-span-2 md:col-span-1 rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-slate-500">Containment</span>
            <select
              value={String(days)}
              onChange={(e) => setDays(e.target.value === "null" ? null : Number(e.target.value))}
              className="rounded border border-slate-300 bg-white px-1 py-0.5 text-[11px] text-slate-600"
            >
              {WINDOWS.map((w) => (
                <option key={w.label} value={String(w.days)}>
                  {w.label}
                </option>
              ))}
            </select>
          </div>
          {/* null containment is an empty window, not 0%: nothing has been handled to score */}
          <div className="text-2xl font-semibold">
            {stats?.containment == null ? "—" : `${Math.round(stats.containment * 100)}%`}
          </div>
          <div className="text-[11px] text-slate-500">
            {stats ? `${stats.contained} / ${stats.contained + stats.needs_person} handled` : "…"}
          </div>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
          <div className="text-xs text-slate-500">Active calls</div>
          <div className="text-2xl font-semibold flex items-center gap-2">
            {live.length}
            {live.length > 0 && <span className="h-2.5 w-2.5 rounded-full bg-red-500 animate-pulse" />}
          </div>
        </div>
        {tile("Open", count("open"), "open", "text-blue-700")}
        {tile("In progress", count("in_progress"), "in_progress", "text-amber-700")}
        {tile("Resolved", count("resolved"), "resolved", "text-emerald-700")}
      </div>

      {live.length > 0 && (
        <section className="mb-6">
          <h2 className="text-sm font-semibold text-slate-700 mb-2">Live calls</h2>
          <div className="grid md:grid-cols-2 gap-3">
            {live.map((c) => {
              const last = c.transcript[c.transcript.length - 1];
              return (
                <Link
                  key={c.id}
                  href={`/calls/${c.id}`}
                  className="block rounded-lg border border-red-200 bg-white p-3 shadow-sm hover:border-red-400"
                >
                  {c.status === "needs_person" && (
                    <div className="-m-3 mb-2 rounded-t-lg bg-amber-50 px-3 py-1.5 text-xs text-amber-800 ring-1 ring-amber-200">
                      Needs a person{c.transfer_reason ? ` · ${c.transfer_reason}` : ""}
                    </div>
                  )}
                  <div className="flex items-center gap-2 text-sm mb-1">
                    <span className="h-2 w-2 rounded-full bg-red-500 animate-pulse" />
                    <span className="font-medium">{c.id}</span>
                    <span className="text-slate-500 font-mono text-xs">{duration(c.started_at, null)}</span>
                    <span className="ml-auto text-xs text-slate-500">
                      {caseIdsOf(c).length ? `→ ${caseIdsOf(c).join(", ")}` : "no case yet"}
                    </span>
                  </div>
                  <p className="text-sm text-slate-700 truncate">
                    {last ? (
                      <>
                        <span className="text-slate-400">{last.role === "user" ? "Caller" : "Agent"}:</span> {last.text}
                      </>
                    ) : (
                      <span className="text-slate-400">Waiting for the first line…</span>
                    )}
                  </p>
                </Link>
              );
            })}
          </div>
        </section>
      )}

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search id, name, phone, description…"
          className="flex-1 min-w-60 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
        />
        <select
          value={issue}
          onChange={(e) => setIssue(e.target.value)}
          className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
        >
          <option value="all">All types</option>
          {ISSUE_TYPES.map((t) => (
            <option key={t} value={t}>
              {humanize(t)}
            </option>
          ))}
        </select>
        {(["all", ...STATUSES] as const).map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`px-3 py-1 rounded-full text-xs border ${
              filter === s ? "bg-slate-900 text-white border-slate-900" : "bg-white text-slate-700 border-slate-300 hover:border-slate-500"
            }`}
          >
            {s === "all" ? "All" : humanize(s)}
          </button>
        ))}
      </div>

      <div className="rounded-lg border border-slate-200 bg-white shadow-sm overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-slate-500 bg-slate-50 border-b border-slate-200">
            <tr>
              <th className="py-2 px-4">Case</th>
              <th className="py-2 px-4">Resident</th>
              <th className="py-2 px-4">Issue</th>
              <th className="py-2 px-4">Status</th>
              <th className="py-2 px-4">Phone</th>
              <th className="py-2 px-4">Updated</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => (
              <tr
                key={c.id}
                className={`border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors duration-700 ${
                  cases.changed.has(c.id) ? "bg-amber-50" : ""
                }`}
              >
                <td className="py-2.5 px-4">
                  <Link href={`/cases/${c.id}`} className="font-medium text-blue-700 hover:underline">
                    {c.id}
                  </Link>
                  {onCall.has(c.id) && (
                    <span className="ml-2 inline-flex items-center gap-1 text-[10px] text-red-700">
                      <span className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" /> on call
                    </span>
                  )}
                </td>
                <td className="py-2.5 px-4">{c.name}</td>
                <td className="py-2.5 px-4">{c.issue_type ? humanize(c.issue_type) : "—"}</td>
                <td className="py-2.5 px-4">
                  <span className={`px-2 py-0.5 rounded-full text-xs ring-1 ${STATUS_COLOR[c.status]}`}>
                    {humanize(c.status)}
                  </span>
                </td>
                <td className="py-2.5 px-4 font-mono text-xs">{c.phone}</td>
                <td className="py-2.5 px-4 text-slate-500" title={new Date(c.updated_at).toLocaleString()}>
                  {ago(c.updated_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!error && shown.length === 0 && refreshedAt && (
          <p className="text-slate-500 text-sm p-4">{rows.length === 0 ? "No cases yet." : "No cases match."}</p>
        )}
      </div>
    </main>
  );
}
