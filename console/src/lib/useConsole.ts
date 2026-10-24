"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getCalls, getCase, getCases } from "./api";
import { supabase } from "./supabase";
import type { Case, CaseEvent } from "./types";

export type Live = "connecting" | "live" | "offline";

const FLASH_MS = 2000; // DASHBOARD.md: highlight changed fields for about 2 s
const NEW_MS = 5000; // how long a new queue row keeps its "new" highlight
const CALL_LOG_HOURS = 3; // how far back the Calls list reaches
const POLL_MS = 2000; // how stale the screen may get when nothing nudges it sooner
const CALLS_RETRY_MS = 30_000; // /api/calls 404s until the API is redeployed; ask again this often

// Every key whose value differs, so the case view can flash exactly what changed.
function changedKeys(before: Case, after: Case): string[] {
  return (Object.keys(after) as (keyof Case)[]).filter(
    (k) => JSON.stringify(before[k]) !== JSON.stringify(after[k]),
  );
}

// The console's data: cases, the selected case's events, and the live call log.
//
// The source of truth is the CityLine API on Fly, polled every POLL_MS: it reads Supabase with
// full access, while the publishable key reads nothing as long as row level security is on.
// Supabase Realtime, when it can see the tables, only nudges an immediate refetch, so a change
// lands in well under a second instead of up to POLL_MS. Each refetch is diffed against what's on
// screen: new rows slide in, changed fields glow.
export function useConsole() {
  const [cases, setCases] = useState<Case[]>([]);
  const [events, setEvents] = useState<CaseEvent[]>([]);
  // every call's log rows (call_started / transcript / call_ended), whether or not it has a case yet
  const [callEvents, setCallEvents] = useState<CaseEvent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [live, setLive] = useState<Live>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<Record<string, Set<string>>>({});
  // ids (cases and events) that just arrived, for the slide-in highlight
  const [fresh, setFresh] = useState<Set<string>>(new Set());

  // the poll loop is bound once; refs let it see the current values
  const casesRef = useRef<Case[] | null>(null); // null until the first load: nothing is "new" then
  const eventIdsRef = useRef<Set<string> | null>(null);
  const callIdsRef = useRef<Set<string> | null>(null);
  const selectedRef = useRef<string | null>(null);
  const callsRetryAt = useRef(0);

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

  const markFresh = useCallback((ids: string[]) => {
    if (!ids.length) return;
    setFresh((s) => new Set([...s, ...ids]));
    setTimeout(() => {
      setFresh((s) => {
        const next = new Set(s);
        ids.forEach((id) => next.delete(id));
        return next;
      });
    }, NEW_MS);
  }, []);

  const loadCases = useCallback(async () => {
    const next = await getCases();
    const before = casesRef.current;
    if (before) {
      const byId = new Map(before.map((c) => [c.id, c]));
      markFresh(next.filter((c) => !byId.has(c.id)).map((c) => c.id));
      for (const c of next) {
        const old = byId.get(c.id);
        if (old) markFlash(c.id, changedKeys(old, c));
      }
    }
    casesRef.current = next;
    setCases(next);
  }, [markFlash, markFresh]);

  const loadEvents = useCallback(async () => {
    const id = selectedRef.current;
    if (!id) return;
    const { events: next } = await getCase(id);
    // a stale answer for a case the caseworker already left must not overwrite the new one
    if (selectedRef.current !== id) return;
    const seen = eventIdsRef.current;
    if (seen) markFresh(next.filter((e) => !seen.has(e.id)).map((e) => e.id));
    eventIdsRef.current = new Set(next.map((e) => e.id));
    setEvents(next);
  }, [markFresh]);

  const loadCalls = useCallback(async () => {
    if (Date.now() < callsRetryAt.current) return;
    try {
      const next = await getCalls(CALL_LOG_HOURS);
      const seen = callIdsRef.current;
      if (seen) markFresh(next.filter((e) => !seen.has(e.id)).map((e) => e.id));
      callIdsRef.current = new Set(next.map((e) => e.id));
      setCallEvents(next);
    } catch (e) {
      // an API without the live call log yet: the Calls list stays empty, the rest still works
      if (e instanceof ApiError && e.status === 404) callsRetryAt.current = Date.now() + CALLS_RETRY_MS;
      else throw e;
    }
  }, [markFresh]);

  const refresh = useCallback(async () => {
    try {
      await Promise.all([loadCases(), loadEvents(), loadCalls()]);
      setError(null);
      setLive("live");
    } catch (e) {
      setError(`Can't reach the CityLine API: ${e instanceof Error ? e.message : String(e)}`);
      setLive("offline");
    }
  }, [loadCases, loadEvents, loadCalls]);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let running = false;
    let again = false;

    // one refetch at a time; a nudge during one queues exactly one more
    async function tick() {
      if (running) {
        again = true;
        return;
      }
      running = true;
      clearTimeout(timer);
      await refresh();
      running = false;
      if (stopped) return;
      if (again) {
        again = false;
        tick();
      } else {
        timer = setTimeout(tick, POLL_MS);
      }
    }
    tick();

    // Realtime as a nudge only (see above); it sees nothing while RLS is on, which is fine
    const channel = supabase
      ?.channel("cityline")
      .on("postgres_changes", { event: "*", schema: "public", table: "cases" }, () => tick())
      .on("postgres_changes", { event: "*", schema: "public", table: "events" }, () => tick())
      .subscribe();

    // a laptop waking up, or the tab coming back to the front: catch up at once
    const onVisible = () => document.visibilityState === "visible" && tick();
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      stopped = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      if (channel) supabase?.removeChannel(channel);
    };
  }, [refresh]);

  const select = useCallback(
    (id: string) => {
      selectedRef.current = id;
      eventIdsRef.current = null; // opening a case shows its history, not a burst of "new"
      setSelectedId(id);
      setEvents([]);
      loadEvents().catch(() => {}); // the poll loop reports errors; don't wait for its next tick
    },
    [loadEvents],
  );

  return { cases, events, callEvents, selectedId, select, live, error, flash, fresh };
}
