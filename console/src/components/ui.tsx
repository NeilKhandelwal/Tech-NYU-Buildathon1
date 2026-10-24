import type { ReactNode } from "react";
import { ISSUE_LABEL, STATUS_LABEL, issueOf, isHeat, type Case, type Priority, type Status } from "@/lib/types";

export function Card({ title, children, className = "", aside }: {
  title: string;
  children: ReactNode;
  className?: string;
  aside?: ReactNode;
}) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white ${className}`}>
      <header className="flex items-center justify-between border-b border-slate-100 px-4 py-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500">{title}</h3>
        {aside}
      </header>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

// Wraps a value that should glow for ~2 s when realtime changes it.
export function Flash({ on, children, className = "" }: { on: boolean; children: ReactNode; className?: string }) {
  return <span className={`rounded transition-colors duration-700 ${on ? "bg-amber-200" : ""} ${className}`}>{children}</span>;
}

const pill = "inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-semibold leading-none whitespace-nowrap";

export function IssueBadge({ c, large }: { c: Case; large?: boolean }) {
  // heat keeps the red family's neighbour (orange) so it reads as "the heat line" at a glance
  const heat = isHeat(c);
  return (
    <span
      className={`${pill} ${large ? "text-sm px-2 py-1" : ""} ${
        heat ? "bg-orange-50 text-orange-800 ring-1 ring-orange-200" : "bg-slate-800 text-white"
      }`}
    >
      {ISSUE_LABEL[issueOf(c)]}
    </span>
  );
}

export function LanguageBadge({ label }: { label: string }) {
  return <span className={`${pill} bg-blue-50 text-blue-900 ring-1 ring-blue-200`}>{label}</span>;
}

export function ViolationBadge({ value, large }: { value: boolean | null; large?: boolean }) {
  const size = large ? "text-sm px-2 py-1" : "";
  if (value === null) return <span className={`${pill} ${size} bg-slate-100 text-slate-500`}>Unchecked</span>;
  return value ? (
    <span className={`${pill} ${size} bg-red-600 text-white`}>Violation: YES</span>
  ) : (
    <span className={`${pill} ${size} bg-slate-100 text-slate-700 ring-1 ring-slate-200`}>Violation: NO</span>
  );
}

const PRIORITY_STYLE: Record<Priority, string> = {
  urgent: "bg-red-50 text-red-700 ring-1 ring-red-300",
  high: "bg-amber-50 text-amber-800 ring-1 ring-amber-300",
  normal: "bg-slate-50 text-slate-600 ring-1 ring-slate-200",
};

export function PriorityBadge({ value, large }: { value: Priority | null; large?: boolean }) {
  const p = value ?? "normal";
  return (
    <span className={`${pill} ${PRIORITY_STYLE[p]} ${large ? "text-sm px-2 py-1" : ""} uppercase`}>
      {p}
    </span>
  );
}

const STATUS_STYLE: Record<Status, string> = {
  intake: "text-slate-500",
  submitted: "text-blue-900",
  in_review: "text-blue-900",
  callback_requested: "text-blue-900",
  called_back: "text-emerald-700",
  resolved: "text-slate-500",
};

export function StatusText({ value }: { value: Status }) {
  return <span className={`font-medium ${STATUS_STYLE[value]}`}>{STATUS_LABEL[value]}</span>;
}

export function Mark() {
  // our own simple mark: a thermometer in a rounded square (no city or agency branding)
  return (
    <svg viewBox="0 0 32 32" className="h-8 w-8 shrink-0" aria-hidden>
      <rect width="32" height="32" rx="7" fill="#1e3a8a" />
      <rect x="14" y="6" width="4" height="14" rx="2" fill="#fff" />
      <circle cx="16" cy="22" r="4.5" fill="#fff" />
      <circle cx="16" cy="22" r="2.5" fill="#ef4444" />
      <rect x="15" y="12" width="2" height="9" fill="#ef4444" />
    </svg>
  );
}
