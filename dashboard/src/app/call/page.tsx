"use client";

import "@livekit/components-styles";
import Link from "next/link";
import { useCallback, useState } from "react";
import {
  BarVisualizer,
  DisconnectButton,
  LiveKitRoom,
  RoomAudioRenderer,
  useVoiceAssistant,
} from "@livekit/components-react";
import {
  CallWithTranscript,
  Grant,
  duration,
  getCall,
  getToken,
  listCallsByRoom,
  useLiveRefresh,
  useNow,
} from "@/lib/api";
import Transcript from "@/components/Transcript";

export default function BrowserCallPage() {
  const [grant, setGrant] = useState<Grant | null>(null);
  // Kept after hangup so the finished transcript and its case link stay on screen.
  const [room, setRoom] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const start = () => {
    setStarting(true);
    setError(null);
    // Timestamped identity: LiveKit disconnects an existing participant with the same one.
    getToken(`resident-${Date.now()}`)
      .then((g) => {
        setGrant(g);
        setRoom(g.room);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setStarting(false));
  };

  return (
    <main className="p-6 max-w-6xl mx-auto w-full">
      <h1 className="text-xl font-semibold">Start a call</h1>
      <p className="text-sm text-slate-500 mb-4">
        Simulates a resident calling the City services line from this browser
      </p>
      {error && <p className="text-red-700 mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm">{error}</p>}

      <div className="grid md:grid-cols-2 gap-4">
        <section className="rounded-lg border border-slate-200 bg-white shadow-sm p-6">
          {!grant ? (
            <div className="flex flex-col items-start gap-3">
              <p className="text-sm text-slate-600">
                Your microphone stays in the browser; the voice agent answers in a fresh room and files the
                case as you talk.
              </p>
              <button
                onClick={start}
                disabled={starting}
                className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
              >
                {starting ? "Connecting…" : "Start call"}
              </button>
            </div>
          ) : (
            <LiveKitRoom
              serverUrl={grant.url}
              token={grant.token}
              connect
              audio
              video={false}
              onDisconnected={() => setGrant(null)}
              onError={(e) => {
                setError(e.message);
                setGrant(null);
              }}
              onMediaDeviceFailure={() => {
                setError("Microphone unavailable — allow mic access for localhost:3000 and start again.");
                setGrant(null);
              }}
            >
              <RoomAudioRenderer />
              <VoicePanel />
            </LiveKitRoom>
          )}
        </section>

        <section>{room ? <LiveTranscript key={room} room={room} /> : <Placeholder />}</section>
      </div>
    </main>
  );
}

const LABELS: Partial<Record<string, string>> = {
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
};

// Inside <LiveKitRoom>: agent audio bars + state, and the hang-up control.
function VoicePanel() {
  const { state, audioTrack } = useVoiceAssistant();

  return (
    <div className="flex flex-col items-center gap-4">
      <BarVisualizer
        state={state}
        track={audioTrack}
        barCount={7}
        className="flex h-28 w-full items-center justify-center gap-1.5"
      />
      <span className="text-sm text-slate-600">{LABELS[state] ?? "Connecting…"}</span>
      <DisconnectButton className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700">
        Hang up
      </DisconnectButton>
    </div>
  );
}

function Placeholder() {
  return (
    <div className="rounded-lg border border-slate-200 bg-white shadow-sm p-4 text-sm text-slate-500">
      The transcript appears here once the call starts.
    </div>
  );
}

// The call record for this room, found by polling /calls?room= plus the WS refresh other pages use.
function LiveTranscript({ room }: { room: string }) {
  const [call, setCall] = useState<CallWithTranscript | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    () =>
      listCallsByRoom(room)
        .then((calls) => (calls.length ? getCall(calls[0].id) : null))
        .then((data) => {
          setCall(data);
          setError(null);
        })
        .catch((e: Error) => setError(e.message)),
    [room],
  );

  useLiveRefresh(load);
  useNow(); // duration ticks every second, not only when data arrives

  if (error) return <p className="text-red-700 text-sm">{error}</p>;
  if (!call)
    return (
      <div className="rounded-lg border border-slate-200 bg-white shadow-sm p-4 text-sm text-slate-500">
        Waiting for the agent to pick up…
      </div>
    );

  return (
    <>
      <div className="flex items-center gap-3 mb-2">
        <Link href={`/calls/${call.id}`} className="font-medium text-blue-700 hover:underline">
          {call.id}
        </Link>
        {call.status === "active" ? (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs bg-red-50 text-red-700 ring-1 ring-red-200">
            <span className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" /> LIVE
          </span>
        ) : (
          <span className="px-2 py-0.5 rounded-full text-xs bg-slate-100 text-slate-600 ring-1 ring-slate-200">Ended</span>
        )}
        <span className="text-sm text-slate-500 font-mono">{duration(call.started_at, call.ended_at)}</span>
        <span className="ml-auto text-sm">
          {call.case_id ? (
            <Link href={`/cases/${call.case_id}`} className="text-blue-700 hover:underline">
              {call.case_id}
            </Link>
          ) : (
            <span className="text-slate-400">no case yet</span>
          )}
        </span>
      </div>
      {call.summary && (
        <p className="mb-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm italic text-slate-600">
          {call.summary}
        </p>
      )}
      <Transcript lines={call.transcript} className="max-h-[60vh] min-h-64" />
    </>
  );
}
