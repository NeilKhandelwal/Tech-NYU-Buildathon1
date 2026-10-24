"use client";

import { useEffect, useMemo, useState } from "react";
import { ActionPanel } from "@/components/ActionPanel";
import { CallView } from "@/components/CallView";
import { CityMap } from "@/components/CityMap";
import {
  BuildingCard,
  CaseHeader,
  ReportCard,
  SummaryCard,
  TranscriptCard,
  ViolationCard,
} from "@/components/CaseCards";
import { Header } from "@/components/Header";
import { LiveTranscript } from "@/components/LiveTranscript";
import { Queue, type Selection } from "@/components/Queue";
import { SmsPanel } from "@/components/SmsPanel";
import { Timeline } from "@/components/Timeline";
import { groupCalls, type Call } from "@/lib/calls";
import { useGeocodes } from "@/lib/geocode";
import { queueOrder } from "@/lib/format";
import { isHeat } from "@/lib/types";
import { useConsole } from "@/lib/useConsole";

const NONE = new Set<string>();

export default function ConsolePage() {
  const { cases, events, callEvents, selectedId, select, live, error, flash, fresh } = useConsole();
  const [now, setNow] = useState(() => Date.now());
  // a call with no case yet is selected by its conversation id; everything else by case id
  const [callId, setCallId] = useState<string | null>(null);

  // the live-call timers tick each second; ages and "avg wait" ride along, no refetch involved
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const calls = useMemo(() => groupCalls(callEvents, now), [callEvents, now]);
  const selectedCall = callId ? calls.find((c) => c.conversationId === callId) : undefined;
  const points = useGeocodes(cases);
  const onLine = useMemo(() => new Set(calls.filter((x) => x.live && x.caseId).map((x) => x.caseId!)), [calls]);
  // an intake call has no place until the agent files its case and the address is geocoded
  const locating = calls.filter((x) => x.live && x.agent === "intake" && !x.caseId).length;

  // the projector should never show an empty panel: open the top of the queue
  useEffect(() => {
    if (!selectedId && !callId && cases.length) select([...cases].sort(queueOrder)[0].id);
  }, [cases, selectedId, callId, select]);

  // a call being watched files its case mid-call: follow it into the case view
  useEffect(() => {
    if (selectedCall?.caseId && cases.some((c) => c.id === selectedCall.caseId)) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- switching views on new data
      setCallId(null);
      select(selectedCall.caseId);
    }
  }, [selectedCall?.caseId, cases, select]);

  function selectCase(id: string) {
    setCallId(null);
    select(id);
  }

  function selectCall(call: Call) {
    if (call.caseId && cases.some((c) => c.id === call.caseId)) return selectCase(call.caseId);
    setCallId(call.conversationId);
  }

  const c = !callId ? cases.find((x) => x.id === selectedId) : undefined;
  const f = (c && flash[c.id]) || NONE;
  // calls on this case, newest first: the live one (if any) leads the case view
  const caseCalls = c ? calls.filter((call) => call.caseId === c.id) : [];
  const liveCall = caseCalls.find((call) => call.live);
  const selection: Selection = callId ? { kind: "call", id: callId } : selectedId ? { kind: "case", id: selectedId } : null;

  return (
    <div className="flex h-screen min-w-[1280px] flex-col">
      <Header cases={cases} live={live} onLine={calls.filter((x) => x.live).length} now={now} />
      {error && <div className="bg-red-50 px-6 py-2 text-sm text-red-800">{error}</div>}
      <div className="flex min-h-0 flex-1">
        <Queue
          cases={cases}
          calls={calls}
          selection={selection}
          onSelectCase={selectCase}
          onSelectCall={selectCall}
          fresh={fresh}
          now={now}
        />
        <main className="flex min-w-0 flex-1 flex-col">
          {selectedCall ? (
            <CallView call={selectedCall} now={now} />
          ) : c ? (
            <>
              <CaseHeader c={c} flash={f} />
              <div className="flex-1 overflow-y-auto p-5">
                {liveCall && (
                  <div className="mb-4">
                    <LiveTranscript call={liveCall} now={now} title="On the line now · live transcript" />
                  </div>
                )}
                <div className="grid grid-cols-[minmax(0,1fr)_380px] gap-4">
                  <div className="space-y-4">
                    <SummaryCard c={c} flash={f} />
                    {isHeat(c) && <BuildingCard c={c} flash={f} />}
                    {c.transcript_original || !caseCalls.length ? (
                      <TranscriptCard key={c.id} c={c} flash={f} />
                    ) : (
                      // no post-call transcript yet: show what the live log caught
                      !liveCall && <LiveTranscript call={caseCalls[0]} now={now} title="Call transcript (live log)" />
                    )}
                    <Timeline events={events} fresh={fresh} />
                  </div>
                  <div className="space-y-4">
                    <CityMap
                      cases={cases}
                      points={points}
                      selectedId={c.id}
                      fresh={fresh}
                      onLine={onLine}
                      locating={locating}
                      onSelect={selectCase}
                    />
                    {isHeat(c) ? <ViolationCard c={c} flash={f} /> : <ReportCard c={c} flash={f} />}
                    <SmsPanel events={events} phone={c.resident_phone} fresh={fresh} />
                  </div>
                </div>
              </div>
              <ActionPanel key={c.id} c={c} events={events} />
            </>
          ) : (
            <div className="m-auto text-slate-400">
              {cases.length || calls.length ? "Select a case or a call." : "Waiting for the first call…"}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
