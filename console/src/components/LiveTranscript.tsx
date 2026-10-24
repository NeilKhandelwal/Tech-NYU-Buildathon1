"use client";

import { useEffect, useRef } from "react";
import { AGENT_LABEL, elapsed, type Call } from "@/lib/calls";
import { nyClock } from "@/lib/format";
import { Card } from "./ui";

export function LiveBadge({ call, now }: { call: Call; now: number }) {
  if (!call.live) return <span className="text-xs text-zinc-400">ended {nyClock(call.endedAt ?? call.startedAt)}</span>;
  return (
    <span className="flex items-center gap-1.5 rounded bg-red-600 px-1.5 py-0.5 text-[11px] font-bold uppercase text-white">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white" />
      Live {elapsed(call.startedAt, now)}
    </span>
  );
}

// One call's lines as they are said: the original words, and the English under them once the
// translation lands (backend/app/live.py writes the line first and the translation after).
export function LiveTranscript({ call, now, title }: { call: Call; now: number; title?: string }) {
  const end = useRef<HTMLDivElement>(null);
  const count = call.lines.length;
  useEffect(() => {
    // follow a live call; leave a finished one where the caseworker scrolled it
    if (call.live) end.current?.scrollIntoView({ block: "nearest" });
  }, [count, call.live]);

  return (
    <Card title={title ?? `${AGENT_LABEL[call.agent]} · transcript`} aside={<LiveBadge call={call} now={now} />}>
      {count === 0 ? (
        <p className="text-sm text-zinc-400">
          {call.live
            ? "Listening… lines appear as they are said (needs ElevenLabs monitoring; otherwise when the call ends)."
            : "No lines were captured live; the full transcript arrives with the post-call summary."}
        </p>
      ) : (
        <div className="max-h-96 space-y-2.5 overflow-y-auto pr-2">
          {call.lines.map((l) => {
            const same = l.english && l.english.trim() === l.original.trim();
            return (
              <div key={l.id} className="flex gap-3">
                <span
                  className={`w-16 shrink-0 pt-0.5 text-[11px] font-semibold uppercase ${
                    l.who === "resident" ? "text-zinc-900" : "text-zinc-400"
                  }`}
                >
                  {l.who === "resident" ? "Resident" : "Agent"}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-[15px] leading-snug text-zinc-900">{l.original}</p>
                  {!same && (
                    <p className={`text-sm leading-snug ${l.english ? "text-zinc-500" : "italic text-zinc-300"}`}>
                      {l.english ?? "translating…"}
                    </p>
                  )}
                </div>
                <span className="shrink-0 pt-0.5 text-[11px] tabular-nums text-zinc-300">{nyClock(l.at)}</span>
              </div>
            );
          })}
          <div ref={end} />
        </div>
      )}
    </Card>
  );
}
