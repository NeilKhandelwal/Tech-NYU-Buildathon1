"use client";

import { useEffect, useMemo, useState } from "react";
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

// case id -> coordinates: the API's stored lat/lon when it has them, else a GeoSearch lookup filled
// in as it lands. Cases without an address are skipped.
export function useGeocodes(cases: Case[]): Record<string, LngLat> {
  // only the browser lookups need state; stored coordinates are read straight off the cases
  const [looked, setLooked] = useState<Record<string, LngLat>>({});
  // the API already geocoded these at filing time: no lookup, and the pin sits where the backend
  // placed the case, so a demo doesn't depend on a third-party call from the browser
  const stored = (c: Case) => c.lat != null && c.lon != null;
  // the effect re-runs only when the set of addresses to look up changes, not on every 2 s poll
  const key = cases.filter((c) => !stored(c)).map((c) => `${c.id}=${placeQuery(c)}`).join("|");

  useEffect(() => {
    let alive = true;
    for (const c of cases) {
      const q = placeQuery(c);
      if (stored(c) || !q) continue;
      lookup(q).then((hit) => {
        if (alive && hit) setLooked((p) => (p[c.id]?.join() === hit.join() ? p : { ...p, [c.id]: hit }));
      });
    }
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- key is the cases' identity here
  }, [key]);

  return useMemo(() => {
    const out = { ...looked };
    for (const c of cases) if (stored(c)) out[c.id] = [c.lon!, c.lat!];
    return out;
  }, [cases, looked]);
}
