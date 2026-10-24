"use client";

import { useEffect, useState } from "react";
import type { Case } from "./types";

export type LngLat = [number, number];

// NYC Planning Labs GeoSearch: the city's own address index, free, no key, CORS open.
const GEOSEARCH = "https://geosearch.planninglabs.nyc/v2/search";
const STORE_KEY = "cityline.geocodes.v1";

// The building, never the unit: the map shows where the problem is, not which apartment.
export const placeQuery = (c: Pick<Case, "address" | "borough">) => {
  const b = c.borough && !c.address.toLowerCase().includes(c.borough.toLowerCase()) ? `, ${c.borough}` : "";
  return `${c.address}${b}`.trim();
};

// null = looked up and not found, so a bad address is asked once, not on every poll
const cache = new Map<string, LngLat | null>(load());
const inflight = new Map<string, Promise<LngLat | null>>();

function load(): [string, LngLat | null][] {
  try {
    return Object.entries(JSON.parse(localStorage.getItem(STORE_KEY) ?? "{}"));
  } catch {
    return []; // SSR, private window, or junk in storage: start empty
  }
}

function save() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(Object.fromEntries(cache)));
  } catch {
    // storage full or blocked: the in-memory cache still covers this session
  }
}

function lookup(q: string): Promise<LngLat | null> {
  if (cache.has(q)) return Promise.resolve(cache.get(q)!);
  if (!inflight.has(q)) {
    const p = fetch(`${GEOSEARCH}?size=1&text=${encodeURIComponent(q)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        const hit: LngLat | null = d?.features?.[0]?.geometry?.coordinates ?? null;
        cache.set(q, hit);
        save();
        return hit;
      })
      // network blip: don't cache the miss, the next poll tries again
      .catch(() => null)
      .finally(() => inflight.delete(q));
    inflight.set(q, p);
  }
  return inflight.get(q)!;
}

// case id -> coordinates, filled in as lookups land. Cases without an address are skipped.
export function useGeocodes(cases: Case[]): Record<string, LngLat> {
  const [points, setPoints] = useState<Record<string, LngLat>>({});
  // the effect re-runs only when the set of addresses changes, not on every 2 s poll
  const key = cases.map((c) => `${c.id}=${placeQuery(c)}`).join("|");

  useEffect(() => {
    let alive = true;
    for (const c of cases) {
      const q = placeQuery(c);
      if (!q) continue;
      lookup(q).then((hit) => {
        if (alive && hit) setPoints((p) => (p[c.id]?.join() === hit.join() ? p : { ...p, [c.id]: hit }));
      });
    }
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- key is the cases' identity here
  }, [key]);

  return points;
}
