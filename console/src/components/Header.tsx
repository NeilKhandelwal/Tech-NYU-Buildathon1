import type { Live } from "@/lib/useConsole";
import type { Case } from "@/lib/types";
import { addressLine, isOpen, minutes, parseTime } from "@/lib/format";
import { IssueBadge, LanguageBadge, Mark } from "./ui";

const LIVE_STYLE: Record<Live, [string, string]> = {
  live: ["bg-emerald-400", "Live"],
  connecting: ["bg-yellow-400", "Connecting"],
  offline: ["bg-red-500", "Offline"],
};

// "Waiting" = cases the city still owes a reply: submitted or in review, not yet actioned
const WAITING = new Set(["submitted", "in_review"]);

// One black bar: who we are, the case on screen, and the desk's numbers. The case's own
// status line sits in the strip below it (CaseHeader), so the case view starts higher.
export function Header({ cases, live, onLine, now, c }: {
  cases: Case[];
  live: Live;
  onLine: number;
  now: number;
  c?: Case; // the case on screen; absent while a call without a case is selected
}) {
  const open = cases.filter(isOpen);
  const urgent = open.filter((x) => x.priority === "urgent").length;
  const waiting = open.filter((x) => WAITING.has(x.status));
  const avgWait = waiting.length
    ? waiting.reduce((sum, x) => sum + (now - parseTime(x.created_at)), 0) / waiting.length
    : null;
  const [dot, label] = LIVE_STYLE[live];

  return (
    <header className="flex h-12 shrink-0 items-center gap-4 bg-zinc-950 px-4 text-white">
      <Mark size="h-7 w-7" />
      <span className="shrink-0 text-[15px] font-semibold tracking-tight">
        CityLine <span className="font-normal text-zinc-400">console (demo)</span>
      </span>
      {c && (
        <div className="flex min-w-0 items-center gap-3 border-l border-zinc-700 pl-4">
          <h2 className="shrink-0 font-mono text-xl font-bold tracking-tight text-yellow-300">{c.case_number}</h2>
          <IssueBadge c={c} />
          <LanguageBadge label={c.language_label ?? c.language} />
          <span className="truncate text-[17px] font-medium text-white" title={addressLine(c)}>
            {addressLine(c)}
          </span>
        </div>
      )}
      <div className="ml-auto flex shrink-0 items-center gap-5 text-xs">
        <span className="flex items-center gap-2 font-medium text-zinc-300">
          <span className={`relative h-2 w-2 rounded-full ${dot}`}>
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
    <span className="flex items-baseline gap-1.5">
      <span className="text-zinc-400">{label}</span>
      <span className={`text-base font-semibold tabular-nums ${alert ? "text-red-400" : "text-white"}`}>{value}</span>
    </span>
  );
}
