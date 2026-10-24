import type { Live } from "@/lib/useConsole";
import type { Case } from "@/lib/types";
import { isOpen, minutes } from "@/lib/format";
import { Mark } from "./ui";

const LIVE_STYLE: Record<Live, [string, string]> = {
  live: ["bg-emerald-500", "Live"],
  connecting: ["bg-amber-400", "Connecting"],
  offline: ["bg-red-500", "Offline"],
};

// "Waiting" = cases the city still owes a reply: submitted or in review, not yet actioned
const WAITING = new Set(["submitted", "in_review"]);

export function Header({ cases, live, onLine, now }: { cases: Case[]; live: Live; onLine: number; now: number }) {
  const open = cases.filter(isOpen);
  const urgent = open.filter((c) => c.priority === "urgent").length;
  const waiting = open.filter((c) => WAITING.has(c.status));
  const avgWait = waiting.length
    ? waiting.reduce((sum, c) => sum + (now - Date.parse(c.created_at)), 0) / waiting.length
    : null;
  const [dot, label] = LIVE_STYLE[live];

  return (
    <header className="flex h-16 shrink-0 items-center gap-6 border-b border-slate-200 bg-white px-6">
      <Mark />
      <h1 className="text-xl font-semibold tracking-tight text-slate-900">
        Heat Check Caseworker Console <span className="font-normal text-slate-400">(demo)</span>
        <span className="ml-3 align-middle text-xs font-medium uppercase tracking-wider text-slate-400">All 311 reports</span>
      </h1>
      <div className="ml-auto flex items-center gap-8 text-sm">
        <span className="flex items-center gap-2 font-medium text-slate-700">
          <span className={`relative h-2.5 w-2.5 rounded-full ${dot}`}>
            {live === "live" && <span className={`absolute inset-0 animate-ping rounded-full ${dot} opacity-60`} />}
          </span>
          {label}
        </span>
        <Stat label="On the line" value={onLine} alert={onLine > 0} />
        <Stat label="Open" value={open.length} />
        <Stat label="Urgent" value={urgent} alert={urgent > 0} />
        <Stat label="Avg wait" value={avgWait === null ? "—" : minutes(avgWait)} />
      </div>
    </header>
  );
}

function Stat({ label, value, alert }: { label: string; value: number | string; alert?: boolean }) {
  return (
    <span className="flex items-baseline gap-2">
      <span className="text-slate-500">{label}</span>
      <span className={`text-lg font-semibold tabular-nums ${alert ? "text-red-600" : "text-slate-900"}`}>{value}</span>
    </span>
  );
}
