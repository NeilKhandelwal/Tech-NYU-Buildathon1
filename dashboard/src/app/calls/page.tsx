"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { Call, ago, caseIdsOf, duration, listCalls, useLiveRefresh, useNow } from "@/lib/api";

type Filter = "all" | "live" | "ended" | "unlinked";

// Same "m:ss" shape duration() renders, for an average that is already in seconds.
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const pct = (n: number, d: number) => (d ? `${Math.round((n / d) * 100)}%` : "—");

export default function CallsPage() {
  const [calls, setCalls] = useState<Call[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  const load = useCallback(
    () =>
      listCalls()
        .then((data) => {
          setCalls(data);
          setError(null);
        })
        .catch((e: Error) => setError(e.message)),
    [],
  );

  useLiveRefresh(load);
  useNow(); // durations tick every second, not only when data arrives

  const liveNow = calls.filter((c) => c.status === "active").length;
  const today = new Date().toDateString();
  const startedToday = calls.filter((c) => new Date(c.started_at).toDateString() === today).length;
  const linked = calls.filter((c) => c.case_id).length;
  const ended = calls.filter((c) => c.status === "ended");
  const lengths = ended
    .filter((c) => c.ended_at)
    // clamp like duration() does, so a bad ended_at can't put a negative number in the tile
    .map((c) => Math.max(0, new Date(c.ended_at!).getTime() - new Date(c.started_at).getTime()) / 1000);
  const avg = lengths.length ? mmss(lengths.reduce((a, b) => a + b, 0) / lengths.length) : "—";
  const summarized = ended.filter((c) => c.summary).length;

  const needle = q.trim().toLowerCase();
  const shown = calls.filter(
    (c) =>
      (filter === "all" ||
        (filter === "live" && c.status === "active") ||
        (filter === "ended" && c.status === "ended") ||
        (filter === "unlinked" && !c.case_id)) &&
      (!needle || `${c.id} ${caseIdsOf(c).join(" ")}`.toLowerCase().includes(needle)),
  );

  const tile = (label: string, value: string | number) => (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="text-2xl font-semibold">{value}</div>
    </div>
  );

  return (
    <main className="p-6 max-w-6xl mx-auto w-full">
      <h1 className="text-xl font-semibold">Calls</h1>
      <p className="text-sm text-slate-500 mb-4">Every voice session, newest first</p>
      {error && <p className="text-red-700 mb-4 text-sm">{error}</p>}

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
        <button
          onClick={() => setFilter(filter === "live" ? "all" : "live")}
          className={`text-left rounded-lg border bg-white px-4 py-3 shadow-sm transition ${
            filter === "live" ? "border-slate-900 ring-1 ring-slate-900" : "border-slate-200 hover:border-slate-400"
          }`}
        >
          <div className="text-xs text-slate-500">Live now</div>
          <div className="text-2xl font-semibold flex items-center gap-2">
            {liveNow}
            {liveNow > 0 && <span className="h-2.5 w-2.5 rounded-full bg-red-500 animate-pulse" />}
          </div>
        </button>
        {tile("Today", startedToday)}
        {tile("Linked", pct(linked, calls.length))}
        {tile("Avg duration", avg)}
        {tile("Summarized", pct(summarized, ended.length))}
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search call or case id"
          className="flex-1 min-w-60 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
        />
        {(["all", "live", "ended", "unlinked"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1 rounded-full text-xs border ${
              filter === f ? "bg-slate-900 text-white border-slate-900" : "bg-white text-slate-700 border-slate-300 hover:border-slate-500"
            }`}
          >
            {f === "all" ? "All" : f[0].toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      <div className="rounded-lg border border-slate-200 bg-white shadow-sm overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-slate-500 bg-slate-50 border-b border-slate-200">
            <tr>
              <th className="py-2 px-4">Call</th>
              <th className="py-2 px-4">Status</th>
              <th className="py-2 px-4">Started</th>
              <th className="py-2 px-4">Duration</th>
              <th className="py-2 px-4">Case</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => (
              <tr key={c.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                <td className="py-2.5 px-4">
                  <Link href={`/calls/${c.id}`} className="font-medium text-blue-700 hover:underline">
                    {c.id}
                  </Link>
                </td>
                <td className="py-2.5 px-4">
                  {c.status === "needs_person" ? (
                    <span
                      title={c.transfer_reason ?? undefined}
                      className="px-2 py-0.5 rounded-full text-xs bg-amber-50 text-amber-800 ring-1 ring-amber-200"
                    >
                      Needs a person
                    </span>
                  ) : c.status === "active" ? (
                    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs bg-red-50 text-red-700 ring-1 ring-red-200">
                      <span className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" /> Live
                    </span>
                  ) : (
                    <span className="px-2 py-0.5 rounded-full text-xs bg-slate-100 text-slate-600 ring-1 ring-slate-200">Ended</span>
                  )}
                </td>
                <td className="py-2.5 px-4 text-slate-500" title={new Date(c.started_at).toLocaleString()}>
                  {ago(c.started_at)}
                </td>
                <td className="py-2.5 px-4 font-mono text-xs">{duration(c.started_at, c.ended_at)}</td>
                <td className="py-2.5 px-4">
                  {caseIdsOf(c).length ? (
                    caseIdsOf(c).map((cid, i) => (
                      <span key={cid}>
                        {i > 0 && ", "}
                        <Link href={`/cases/${cid}`} className="text-blue-700 hover:underline">
                          {cid}
                        </Link>
                      </span>
                    ))
                  ) : (
                    <span className="text-slate-400">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!error && shown.length === 0 && (
          <p className="text-slate-500 text-sm p-4">{calls.length === 0 ? "No calls yet." : "No calls match."}</p>
        )}
      </div>
    </main>
  );
}
