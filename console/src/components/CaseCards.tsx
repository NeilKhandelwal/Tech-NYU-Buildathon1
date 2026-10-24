"use client";

import { useState } from "react";
import type { Case } from "@/lib/types";
import { ISSUE_LABEL, RESPONSE_LABEL, issueOf, isHeat } from "@/lib/types";
import { addressLine, fineRange, legalMinimum, nyTime } from "@/lib/format";
import { Card, Flash, IssueBadge, LanguageBadge, PriorityBadge, StatusText, ViolationBadge } from "./ui";

type Flashing = Set<string>;

export function CaseHeader({ c, flash }: { c: Case; flash: Flashing }) {
  return (
    <div className="border-b border-slate-200 bg-white px-6 py-4">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h2 className="font-mono text-3xl font-bold tracking-tight text-slate-900">{c.case_number}</h2>
        <IssueBadge c={c} large />
        <LanguageBadge label={c.language_label ?? c.language} />
        <span className="text-2xl font-medium text-slate-800">{addressLine(c)}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-6 gap-y-2 text-base text-slate-600">
        <span>
          Status: <Flash on={flash.has("status")}><StatusText value={c.status} /></Flash>
        </span>
        <span className="flex items-center gap-2">
          Priority: <Flash on={flash.has("priority")}><PriorityBadge value={c.priority} large /></Flash>
        </span>
        {isHeat(c) && <Flash on={flash.has("is_violation")}><ViolationBadge value={c.is_violation} large /></Flash>}
        <span className="text-slate-400">·</span>
        <span>{c.resident_name ?? "Name not given"}</span>
        <span className="text-slate-400">Reported {nyTime(c.created_at)}</span>
        {c.resident_response && (
          <Flash on={flash.has("resident_response")} className="ml-auto">
            <span className="rounded bg-emerald-50 px-2 py-1 text-sm font-semibold text-emerald-800 ring-1 ring-emerald-200">
              Resident: {RESPONSE_LABEL[c.resident_response] ?? c.resident_response}
            </span>
          </Flash>
        )}
      </div>
    </div>
  );
}

export function SummaryCard({ c, flash }: { c: Case; flash: Flashing }) {
  return (
    <Card title="Summary (English)">
      <Flash on={flash.has("summary_en")} className="block">
        <p className="text-[15px] leading-relaxed text-slate-800">
          {c.summary_en ?? <span className="text-slate-400">Summary arrives when the call ends.</span>}
        </p>
      </Flash>
      <dl className="mt-3 grid grid-cols-3 gap-2 text-sm">
        {isHeat(c) ? (
          <Fact label="Days without heat" value={c.days_without_heat} />
        ) : (
          <Fact label="Problem" value={ISSUE_LABEL[issueOf(c)]} />
        )}
        <Fact label="Callback number" value={formatPhone(c.resident_phone)} />
        <Fact label="Resident note" value={c.resident_response_note_en} />
      </dl>
    </Card>
  );
}

// What the resident reported, for every 311 type that isn't heat (heat has the violation card).
export function ReportCard({ c, flash }: { c: Case; flash: Flashing }) {
  return (
    <Card title="Report" aside={<IssueBadge c={c} />}>
      <Flash on={flash.has("description")} className="block">
        <p className="text-[17px] leading-relaxed text-slate-900">
          {c.description ?? <span className="text-slate-400">No description yet.</span>}
        </p>
      </Flash>
      <dl className="mt-3 space-y-2 text-sm">
        <Fact label="Where" value={addressLine(c)} />
        <Fact label="Reported" value={nyTime(c.created_at)} />
      </dl>
    </Card>
  );
}

function Fact({ label, value }: { label: string; value: string | number | null }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="font-medium text-slate-800">{value ?? "—"}</dd>
    </div>
  );
}

function formatPhone(p: string) {
  const d = p.replace(/\D/g, "").slice(-10);
  return d.length === 10 ? `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}` : p;
}

export function ViolationCard({ c, flash }: { c: Case; flash: Flashing }) {
  const min = legalMinimum(c.rule_applied);
  return (
    <Card title="Violation check" aside={<ViolationBadge value={c.is_violation} />}>
      <div className="flex gap-6">
        <Temp label="Inside" value={c.indoor_f} hot={flash.has("indoor_f")} alert={!!c.is_violation} />
        <Temp label="Outside" value={c.outdoor_f} hot={flash.has("outdoor_f")} />
        <Temp label="Legal minimum" value={min} />
      </div>
      {min !== null && c.indoor_f !== null && <TempBar indoor={c.indoor_f} min={min} />}
      <Flash on={flash.has("rule_applied")} className="mt-3 block">
        <p className="text-sm text-slate-700">{c.rule_applied ?? "Not checked yet."}</p>
      </Flash>
      {c.is_violation && (
        <p className="mt-2 text-sm">
          <span className="text-slate-500">Fine exposure </span>
          <span className="font-semibold text-red-700">{fineRange(c)}</span>
        </p>
      )}
    </Card>
  );
}

function Temp({ label, value, hot, alert }: { label: string; value: number | null; hot?: boolean; alert?: boolean }) {
  return (
    <div>
      <div className="text-xs text-slate-500">{label}</div>
      <Flash on={!!hot}>
        <span className={`text-3xl font-semibold tabular-nums ${alert ? "text-red-600" : "text-slate-900"}`}>
          {value === null ? "—" : `${Math.round(value)}°F`}
        </span>
      </Flash>
    </div>
  );
}

// Indoor temperature against the legal minimum on a fixed 45-80°F scale.
function TempBar({ indoor, min }: { indoor: number; min: number }) {
  const pct = (f: number) => `${Math.min(100, Math.max(0, ((f - 45) / 35) * 100))}%`;
  const below = indoor < min;
  return (
    <div className="mt-3">
      <div className="relative h-3 rounded-full bg-slate-100">
        <div className={`h-3 rounded-full ${below ? "bg-red-500" : "bg-blue-900"}`} style={{ width: pct(indoor) }} />
        <div className="absolute -top-1 h-5 w-0.5 bg-slate-900" style={{ left: pct(min) }} />
      </div>
      <div className="relative mt-1 h-4 text-[11px] text-slate-500">
        <span className="absolute -translate-x-1/2" style={{ left: pct(min) }}>min {min}°F</span>
      </div>
    </div>
  );
}

export function BuildingCard({ c, flash }: { c: Case; flash: Flashing }) {
  const b = c.building;
  return (
    <Card
      title="Building history"
      aside={b?.repeat_offender && (
        <span className="rounded bg-red-600 px-1.5 py-0.5 text-[11px] font-bold uppercase text-white">Repeat offender</span>
      )}
    >
      <Flash on={flash.has("building")} className="block">
        {!b ? (
          <p className="text-sm text-slate-400">Looking up NYC Open Data after the call…</p>
        ) : (
          <>
            <div className="flex gap-8">
              <Count label="Open violations" value={b.open_violations} />
              <Count label="Class C (hazardous)" value={b.open_class_c} />
              <Count label="Heat complaints last season" value={b.heat_complaints_last_season} />
            </div>
            {!!b.recent?.length && (
              <ul className="mt-3 space-y-1 text-xs text-slate-600">
                {b.recent.slice(0, 3).map((r, i) => (
                  <li key={i} className="truncate">
                    <span className="font-mono text-slate-400">{r.date}</span>{" "}
                    <span className="font-semibold">Class {r.class}</span> {r.description}
                  </li>
                ))}
              </ul>
            )}
            {!!b.source_urls?.length && (
              <p className="mt-2 text-xs">
                <a href={b.source_urls[0]} target="_blank" rel="noreferrer" className="text-blue-900 underline">
                  Source: NYC Open Data
                </a>
              </p>
            )}
          </>
        )}
      </Flash>
    </Card>
  );
}

function Count({ label, value }: { label: string; value?: number }) {
  return (
    <div>
      <div className="text-2xl font-semibold tabular-nums text-slate-900">{value ?? "—"}</div>
      <div className="text-xs text-slate-500">{label}</div>
    </div>
  );
}

export function TranscriptCard({ c, flash }: { c: Case; flash: Flashing }) {
  const [tab, setTab] = useState<"original" | "en">("original");
  const text = tab === "original" ? c.transcript_original : c.transcript_en;
  const tabs = (
    <div className="flex gap-1">
      {(["original", "en"] as const).map((t) => (
        <button
          key={t}
          onClick={() => setTab(t)}
          className={`rounded px-2 py-0.5 text-xs font-medium ${
            tab === t ? "bg-blue-900 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
          }`}
        >
          {t === "original" ? `Original (${c.language_label ?? c.language})` : "English"}
        </button>
      ))}
    </div>
  );
  return (
    <Card title="Transcript" aside={tabs}>
      <Flash on={flash.has(tab === "original" ? "transcript_original" : "transcript_en")} className="block">
        {text ? (
          <div className="max-h-72 space-y-1.5 overflow-y-auto pr-2 text-[15px] leading-relaxed">
            {text.split("\n").filter(Boolean).map((line, i) => <Line key={i} line={line} />)}
          </div>
        ) : (
          <p className="text-sm text-slate-400">The transcript arrives when the call ends.</p>
        )}
      </Flash>
    </Card>
  );
}

// transcript_text() writes "Agent: …" / "Resident: …" lines; the English translation keeps them
function Line({ line }: { line: string }) {
  const m = line.match(/^(Agent|Resident):\s*(.*)$/);
  if (!m) return <p className="text-slate-800">{line}</p>;
  const resident = m[1] === "Resident";
  return (
    <p className="text-slate-800">
      <span className={`mr-2 text-xs font-semibold uppercase ${resident ? "text-blue-900" : "text-slate-400"}`}>
        {resident ? "Resident" : "Agent"}
      </span>
      {m[2]}
    </p>
  );
}
