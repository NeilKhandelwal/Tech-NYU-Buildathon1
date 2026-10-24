"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { supabase } from "./supabase";
import type { Case, CaseEvent, EventType } from "./types";

export type Live = "connecting" | "live" | "offline";

const FLASH_MS = 2000; // DASHBOARD.md: highlight changed fields for about 2 s
const NEW_MS = 5000; // how long a new queue row keeps its "new" highlight

// Every key whose value differs, so the case view can flash exactly what changed.
function changedKeys(before: Case, after: Case): string[] {
  return (Object.keys(after) as (keyof Case)[]).filter(
    (k) => JSON.stringify(before[k]) !== JSON.stringify(after[k]),
  );
}

export function useConsole() {
  const [cases, setCases] = useState<Case[]>([]);
  const [events, setEvents] = useState<CaseEvent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [live, setLive] = useState<Live>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<Record<string, Set<string>>>({});
  // ids (cases and events) that just arrived over realtime, for the slide-in highlight
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  // each case's newest message direction; a case whose newest is sms_in is waiting on us
  const [lastMsg, setLastMsg] = useState<Record<string, EventType>>({});

  // the realtime callbacks are bound once; refs let them see the current values
  const casesRef = useRef<Case[]>([]);
  const selectedRef = useRef<string | null>(null);
  useEffect(() => {
    casesRef.current = cases;
  }, [cases]);
  useEffect(() => {
    selectedRef.current = selectedId;
  }, [selectedId]);

  const markFlash = useCallback((id: string, keys: string[]) => {
    if (!keys.length) return;
    setFlash((f) => ({ ...f, [id]: new Set([...(f[id] ?? []), ...keys]) }));
    setTimeout(() => {
      setFlash((f) => {
        const next = new Set(f[id]);
        keys.forEach((k) => next.delete(k));
        return { ...f, [id]: next };
      });
    }, FLASH_MS);
  }, []);

  const markFresh = useCallback((id: string) => {
    setFresh((s) => new Set(s).add(id));
    setTimeout(() => {
      setFresh((s) => {
        const next = new Set(s);
        next.delete(id);
        return next;
      });
    }, NEW_MS);
  }, []);

  const loadCases = useCallback(async () => {
    if (!supabase) return;
    const { data, error } = await supabase.from("cases").select("*");
    if (error) return setError(`Could not read cases: ${error.message}`);
    setError(null);
    setCases(data as Case[]);
  }, []);

  // one query over every case's messages, so the queue can badge cases nobody has opened
  const loadLastMsg = useCallback(async () => {
    if (!supabase) return;
    const { data, error } = await supabase
      .from("events")
      .select("case_id, type")
      .in("type", ["sms_in", "sms_out"])
      .order("created_at", { ascending: true });
    if (error) return setError(`Could not read messages: ${error.message}`);
    const last: Record<string, EventType> = {};
    for (const e of data as Pick<CaseEvent, "case_id" | "type">[]) last[e.case_id] = e.type;
    setLastMsg(last);
  }, []);

  const loadEvents = useCallback(async (caseId: string) => {
    if (!supabase) return;
    const { data, error } = await supabase
      .from("events")
      .select("*")
      .eq("case_id", caseId)
      .order("created_at", { ascending: true });
    if (error) return setError(`Could not read events: ${error.message}`);
    // a stale answer for a case the caseworker already left must not overwrite the new one
    if (selectedRef.current === caseId) setEvents(data as CaseEvent[]);
  }, []);

  useEffect(() => {
    if (!supabase) return;
    const client = supabase;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount
    loadCases();
    loadLastMsg();

    const channel = client
      .channel("hc")
      .on("postgres_changes", { event: "*", schema: "public", table: "cases" }, (msg) => {
        if (msg.eventType === "DELETE") {
          const gone = (msg.old as Partial<Case>).id;
          setCases((cs) => cs.filter((c) => c.id !== gone));
          return;
        }
        const row = msg.new as Case;
        const before = casesRef.current.find((c) => c.id === row.id);
        if (before) markFlash(row.id, changedKeys(before, row));
        else markFresh(row.id);
        setCases((cs) => (before ? cs.map((c) => (c.id === row.id ? row : c)) : [row, ...cs]));
      })
      .on("postgres_changes", { event: "*", schema: "public", table: "events" }, (msg) => {
        const row = msg.new as CaseEvent;
        if (msg.eventType === "INSERT" && (row.type === "sms_in" || row.type === "sms_out")) {
          setLastMsg((m) => ({ ...m, [row.case_id]: row.type }));
        }
        if (msg.eventType !== "INSERT" || row.case_id !== selectedRef.current) return;
        markFresh(row.id); // only live arrivals animate, not the history loaded on open
        setEvents((es) => (es.some((e) => e.id === row.id) ? es : [...es, row]));
      })
      .subscribe((status) => {
        if (status === "SUBSCRIBED") {
          setLive("live");
          // anything that changed while the socket was down arrives in one refetch
          loadCases();
          loadLastMsg();
          if (selectedRef.current) loadEvents(selectedRef.current);
        } else if (status === "CHANNEL_ERROR" || status === "TIMED_OUT" || status === "CLOSED") {
          setLive("offline");
        }
      });

    return () => {
      client.removeChannel(channel);
    };
  }, [loadCases, loadEvents, loadLastMsg, markFlash, markFresh]);

  const select = useCallback(
    (id: string) => {
      selectedRef.current = id;
      setSelectedId(id);
      setEvents([]);
      loadEvents(id);
    },
    [loadEvents],
  );

  const replied = new Set(Object.keys(lastMsg).filter((id) => lastMsg[id] === "sms_in"));

  return { cases, events, selectedId, select, live, error, flash, fresh, replied, configured: !!supabase };
}
