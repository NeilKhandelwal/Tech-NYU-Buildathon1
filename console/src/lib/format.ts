import type { Case, Priority } from "./types";

const NY = "America/New_York";

export function ago(iso: string, now: number) {
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function minutes(ms: number) {
  const m = Math.round(ms / 60000);
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function nyTime(iso: string) {
  return new Date(iso).toLocaleString("en-US", {
    timeZone: NY,
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function nyClock(iso: string) {
  return new Date(iso).toLocaleTimeString("en-US", { timeZone: NY, hour: "numeric", minute: "2-digit" });
}

const RANK: Record<Priority, number> = { urgent: 0, high: 1, normal: 2 };

// DASHBOARD.md: urgent, then high, then newest
export function queueOrder(a: Case, b: Case) {
  const p = RANK[a.priority ?? "normal"] - RANK[b.priority ?? "normal"];
  return p !== 0 ? p : Date.parse(b.created_at) - Date.parse(a.created_at);
}

// The legal minimum the backend's rule_text() wrote, e.g. "... inside 61F < 68F" -> 68.
// Rules that impose no minimum don't end in a temperature, so they give null.
export function legalMinimum(rule: string | null): number | null {
  const m = rule?.match(/(?:<|>=)\s*(\d+)F$/);
  return m ? Number(m[1]) : null;
}

// CLAUDE.md "The law": $250-$500/day first violation, $500-$1,000/day repeat at the building
export function fineRange(c: Case) {
  return c.building?.repeat_offender ? "$500–$1,000 / day (repeat building)" : "$250–$500 / day";
}

export function addressLine(c: Case) {
  return [c.address, c.unit && `Apt ${c.unit}`, c.borough].filter(Boolean).join(", ");
}

export function isOpen(c: Case) {
  return c.status !== "resolved";
}
