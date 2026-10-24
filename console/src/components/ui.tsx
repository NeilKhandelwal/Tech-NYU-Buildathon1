import type { ReactNode } from "react";
import { COMPLAINT_LABEL, STATUS_LABEL, complaintOf, isHeat, type Case, type Priority, type Status } from "@/lib/types";

export function Card({ title, children, className = "", aside, flush }: {
  title: string;
  children: ReactNode;
  className?: string;
  aside?: ReactNode;
  flush?: boolean; // content runs edge to edge (the map), no inner padding
}) {
  return (
    <section className={`rounded-lg border border-zinc-200 bg-white ${className}`}>
      <header className="flex items-center justify-between border-b border-zinc-100 px-4 py-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">{title}</h3>
        {aside}
      </header>
      <div className={flush ? "" : "px-4 py-3"}>{children}</div>
    </section>
  );
}

// Wraps a value that should glow for ~2 s when realtime changes it.
export function Flash({ on, children, className = "" }: { on: boolean; children: ReactNode; className?: string }) {
  return <span className={`rounded transition-colors duration-700 ${on ? "bg-yellow-200" : ""} ${className}`}>{children}</span>;
}

const pill = "inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-semibold leading-none whitespace-nowrap";

export function IssueBadge({ c, large }: { c: Case; large?: boolean }) {
  // heat keeps the red family's neighbour (orange) so it reads as "the heat line" at a glance
  const heat = isHeat(c);
  return (
    <span
      className={`${pill} ${large ? "text-sm px-2 py-1" : ""} ${
        heat ? "bg-orange-50 text-orange-800 ring-1 ring-orange-200" : "bg-zinc-800 text-white"
      }`}
    >
      {COMPLAINT_LABEL[complaintOf(c)]}
    </span>
  );
}

export function LanguageBadge({ label }: { label: string }) {
  return <span className={`${pill} bg-zinc-100 text-zinc-800 ring-1 ring-zinc-300`}>{label}</span>;
}

export function ViolationBadge({ value, large }: { value: boolean | null; large?: boolean }) {
  const size = large ? "text-sm px-2 py-1" : "";
  if (value === null) return <span className={`${pill} ${size} bg-zinc-100 text-zinc-500`}>Unchecked</span>;
  return value ? (
    <span className={`${pill} ${size} bg-red-600 text-white`}>Violation: YES</span>
  ) : (
    <span className={`${pill} ${size} bg-zinc-100 text-zinc-700 ring-1 ring-zinc-200`}>Violation: NO</span>
  );
}

const PRIORITY_STYLE: Record<Priority, string> = {
  urgent: "bg-red-50 text-red-700 ring-1 ring-red-300",
  high: "bg-amber-50 text-amber-800 ring-1 ring-amber-300",
  normal: "bg-zinc-50 text-zinc-600 ring-1 ring-zinc-200",
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
  intake: "text-zinc-500",
  submitted: "text-zinc-900",
  in_review: "text-zinc-900",
  callback_requested: "text-zinc-900",
  called_back: "text-emerald-700",
  resolved: "text-zinc-500",
};

export function StatusText({ value }: { value: Status }) {
  return <span className={`font-medium ${STATUS_STYLE[value]}`}>{STATUS_LABEL[value]}</span>;
}

export function Mark({ size = "h-8 w-8" }: { size?: string }) {
  // our own simple mark: a thermometer in a rounded square (no city or agency branding)
  return (
    <svg viewBox="0 0 32 32" className={`${size} shrink-0`} aria-hidden>
      <rect width="32" height="32" rx="7" fill="#facc15" />
      <rect x="14" y="6" width="4" height="14" rx="2" fill="#09090b" />
      <circle cx="16" cy="22" r="4.5" fill="#09090b" />
      <circle cx="16" cy="22" r="2.5" fill="#facc15" />
      <rect x="15" y="12" width="2" height="9" fill="#facc15" />
    </svg>
  );
}
