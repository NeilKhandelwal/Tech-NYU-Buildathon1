"use client";

import { useEffect, useState } from "react";
import { ActionPanel } from "@/components/ActionPanel";
import { BuildingCard, CaseHeader, SummaryCard, TranscriptCard, ViolationCard } from "@/components/CaseCards";
import { Header } from "@/components/Header";
import { Queue } from "@/components/Queue";
import { SmsPanel } from "@/components/SmsPanel";
import { Timeline } from "@/components/Timeline";
import { queueOrder } from "@/lib/format";
import { useConsole } from "@/lib/useConsole";

const NONE = new Set<string>();

export default function ConsolePage() {
  const { cases, events, selectedId, select, live, error, flash, fresh, configured } = useConsole();
  const [now, setNow] = useState(() => Date.now());

  // ages and "avg wait" tick on their own; no data refetch involved
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  // the projector should never show an empty case panel: open the top of the queue
  useEffect(() => {
    if (!selectedId && cases.length) select([...cases].sort(queueOrder)[0].id);
  }, [cases, selectedId, select]);

  const c = cases.find((x) => x.id === selectedId);
  const f = (c && flash[c.id]) || NONE;

  if (!configured) {
    return (
      <main className="m-auto max-w-lg p-8 text-slate-700">
        <h1 className="text-lg font-semibold">Supabase isn&apos;t configured</h1>
        <p className="mt-2 text-sm">
          Set <code>NEXT_PUBLIC_SUPABASE_URL</code> and <code>NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY</code> in{" "}
          <code>console/.env.local</code> (or the Vercel project) and restart.
        </p>
      </main>
    );
  }

  return (
    <div className="flex h-screen min-w-[1280px] flex-col">
      <Header cases={cases} live={live} now={now} />
      {error && <div className="bg-red-50 px-6 py-2 text-sm text-red-800">{error}</div>}
      <div className="flex min-h-0 flex-1">
        <Queue cases={cases} selectedId={selectedId} onSelect={select} fresh={fresh} now={now} />
        <main className="flex min-w-0 flex-1 flex-col">
          {c ? (
            <>
              <CaseHeader c={c} flash={f} />
              <div className="flex-1 overflow-y-auto p-5">
                <div className="grid grid-cols-[minmax(0,1fr)_380px] gap-4">
                  <div className="space-y-4">
                    <SummaryCard c={c} flash={f} />
                    <BuildingCard c={c} flash={f} />
                    <TranscriptCard key={c.id} c={c} flash={f} />
                    <Timeline events={events} />
                  </div>
                  <div className="space-y-4">
                    <ViolationCard c={c} flash={f} />
                    <SmsPanel events={events} phone={c.resident_phone} />
                  </div>
                </div>
              </div>
              <ActionPanel key={c.id} c={c} events={events} />
            </>
          ) : (
            <div className="m-auto text-slate-400">{cases.length ? "Select a case." : "Waiting for the first case…"}</div>
          )}
        </main>
      </div>
    </div>
  );
}
