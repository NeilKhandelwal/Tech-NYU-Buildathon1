"use client";

import { useEffect, useRef, useState } from "react";
import { TranscriptLine } from "@/lib/api";

// Chat-style list: caller on the right, agent on the left.
// Auto-scrolls only when you were already at the bottom, so reading back doesn't get yanked;
// otherwise the new lines are counted in a pill you can click to catch up.
export default function Transcript({
  lines,
  className = "max-h-[60vh]",
  active = false,
}: {
  lines: TranscriptLine[];
  className?: string;
  active?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true); // was the box within 40px of the bottom before the new line?
  const prevLen = useRef(0);
  const [behind, setBehind] = useState(0);

  const toBottom = () => {
    const el = box.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    stick.current = true;
    setBehind(0);
  };

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const added = lines.length - prevLen.current;
    prevLen.current = lines.length;
    if (stick.current) {
      el.scrollTop = el.scrollHeight;
      setBehind(0);
    } else if (added > 0) {
      setBehind((n) => n + added);
    }
  }, [lines.length]);

  const onScroll = () => {
    const el = box.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (stick.current) setBehind(0);
  };

  // Whoever didn't speak last is the one we're waiting on.
  const typingIsUser = lines.length > 0 && lines[lines.length - 1].role === "agent";

  return (
    <div className="relative">
      <div
        ref={box}
        onScroll={onScroll}
        className={`${className} overflow-y-auto space-y-3 p-4 rounded-lg bg-slate-50 border border-slate-200`}
      >
        {lines.length === 0 && <p className="text-slate-500 text-sm">Waiting for the first line…</p>}
        {lines.map((l) => {
          const user = l.role === "user";
          return (
            <div key={l.id} className={`flex ${user ? "justify-end" : "justify-start"}`}>
              <div
                className={`max-w-[80%] rounded-2xl px-3.5 py-2 text-sm shadow-sm ${
                  user ? "bg-blue-600 text-white rounded-br-sm" : "bg-white text-slate-900 border border-slate-200 rounded-bl-sm"
                }`}
              >
                <div className={`text-[10px] mb-0.5 ${user ? "text-blue-100" : "text-slate-500"}`}>
                  {user ? "Caller" : "Agent"} · {new Date(l.ts).toLocaleTimeString()}
                </div>
                {l.text}
              </div>
            </div>
          );
        })}
        {active && (
          <div className={`flex ${typingIsUser ? "justify-end" : "justify-start"}`}>
            <div
              className={`rounded-2xl px-3.5 py-2 text-sm shadow-sm animate-pulse ${
                typingIsUser ? "bg-blue-600 text-white rounded-br-sm" : "bg-white text-slate-500 border border-slate-200 rounded-bl-sm"
              }`}
            >
              …
            </div>
          </div>
        )}
      </div>
      {behind > 0 && (
        <button
          onClick={toBottom}
          className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full bg-slate-900 text-white text-xs px-3 py-1 shadow-lg"
        >
          ↓ {behind} new {behind === 1 ? "line" : "lines"}
        </button>
      )}
    </div>
  );
}
