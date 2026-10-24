"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ConversationProvider, useConversation } from "@elevenlabs/react";
import {
  CallWithTranscript,
  duration,
  getCall,
  patchCall,
  postTranscript,
  startVoiceSession,
  useLiveRefresh,
  useNow,
} from "@/lib/api";
import Transcript from "@/components/Transcript";

export default function BrowserCallPage() {
  return (
    <ConversationProvider>
      <CallDesk />
    </ConversationProvider>
  );
}

function CallDesk() {
  // Kept after hangup so the finished transcript and its case link stay on screen.
  const [callId, setCallId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  // The SDK callbacks are registered once per session, so they read the call id from a ref.
  const callRef = useRef<string | null>(null);

  const conversation = useConversation({
    onMessage: ({ role, message }) => {
      const id = callRef.current;
      // a failed relay is not worth interrupting the call for: the post-call webhook backfills
      if (id && message.trim()) postTranscript(id, role, message.trim()).catch(() => {});
    },
    onDisconnect: () => {
      const id = callRef.current;
      // the hang-up is what drops the call from "Live calls"; the webhook ends it if this fails
      if (id) patchCall(id, { status: "ended" }).catch(() => {});
    },
    onError: (message) => setError(message),
  });

  const live = conversation.status === "connected" || conversation.status === "connecting";

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      // ask first, so a blocked mic is our clear message rather than a failed session
      await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError("Microphone unavailable — allow mic access for this page and start again.");
      setStarting(false);
      return;
    }
    try {
      const session = await startVoiceSession();
      callRef.current = session.call_id;
      setCallId(session.call_id);
      conversation.startSession({
        conversationToken: session.token,
        connectionType: "webrtc",
        // every server tool reads {{call_id}}, so the agent's writes land on this call
        dynamicVariables: { call_id: session.call_id },
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
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
          {!live ? (
            <div className="flex flex-col items-start gap-3">
              <p className="text-sm text-slate-600">
                Talk to the ElevenLabs voice agent from your microphone; it files the case as you talk.
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
            <VoicePanel
              status={conversation.status}
              isSpeaking={conversation.isSpeaking}
              level={conversation.isSpeaking ? conversation.getOutputVolume : conversation.getInputVolume}
              onHangUp={conversation.endSession}
            />
          )}
        </section>

        <section>{callId ? <LiveTranscript key={callId} callId={callId} /> : <Placeholder />}</section>
      </div>
    </main>
  );
}

const BARS = [0.5, 0.75, 0.9, 1, 0.9, 0.75, 0.5]; // a centred hump, so a single level reads as a voice

// Agent audio level + state, and the hang-up control.
function VoicePanel({
  status,
  isSpeaking,
  level,
  onHangUp,
}: {
  status: string;
  isSpeaking: boolean;
  level: () => number;
  onHangUp: () => void;
}) {
  const [volume, setVolume] = useState(0);

  useEffect(() => {
    let frame = 0;
    const tick = () => {
      setVolume(Math.min(1, level() * 2)); // raw levels sit low; doubling fills the bars on speech
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [level]);

  const label = status === "connecting" ? "Connecting…" : isSpeaking ? "Speaking" : "Listening";

  return (
    <div className="flex flex-col items-center gap-4">
      <div className="flex h-28 w-full items-center justify-center gap-1.5">
        {BARS.map((shape, i) => (
          <span
            key={i}
            className={`w-3 rounded-full transition-[height] duration-75 ${isSpeaking ? "bg-slate-900" : "bg-slate-400"}`}
            style={{ height: `${12 + shape * volume * 100}px` }}
          />
        ))}
      </div>
      <span className="text-sm text-slate-600">{label}</span>
      <button
        onClick={onHangUp}
        className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
      >
        Hang up
      </button>
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

// The call record, refetched on the same WS refresh other pages use.
function LiveTranscript({ callId }: { callId: string }) {
  const [call, setCall] = useState<CallWithTranscript | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    () =>
      getCall(callId)
        .then((data) => {
          setCall(data);
          setError(null);
        })
        .catch((e: Error) => setError(e.message)),
    [callId],
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
        {call.status === "ended" ? (
          <span className="px-2 py-0.5 rounded-full text-xs bg-slate-100 text-slate-600 ring-1 ring-slate-200">Ended</span>
        ) : (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs bg-red-50 text-red-700 ring-1 ring-red-200">
            <span className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" /> LIVE
          </span>
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
