import { AGENT_LABEL, type Call } from "@/lib/calls";
import { nyTime } from "@/lib/format";
import { LiveBadge, LiveTranscript } from "./LiveTranscript";

// A call that hasn't filed a case (yet): the agent is still collecting details, or the caller
// hung up first. Once create_case runs the call joins its case and the page switches to it.
export function CallView({ call, now }: { call: Call; now: number }) {
  return (
    <>
      <div className="border-b border-slate-200 bg-white px-6 py-4">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h2 className="text-3xl font-bold tracking-tight text-slate-900">{AGENT_LABEL[call.agent]}</h2>
          <LiveBadge call={call} now={now} />
        </div>
        <div className="mt-2 flex flex-wrap gap-x-6 text-base text-slate-600">
          <span>Caller: {call.caller ? `…${call.caller.slice(-4)}` : "number not shown"}</span>
          <span className="text-slate-400">Started {nyTime(call.startedAt)}</span>
          <span className="text-slate-400">
            {call.live ? "No case yet: the agent is still collecting details" : "Ended without filing a case"}
          </span>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-5">
        <LiveTranscript call={call} now={now} title="Live transcript" />
      </div>
    </>
  );
}
