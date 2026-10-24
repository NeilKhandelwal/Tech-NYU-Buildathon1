"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import type { Map as MlMap, Marker } from "maplibre-gl";
import type { LngLat } from "@/lib/geocode";
import { COMPLAINT_LABEL, complaintOf, type Case } from "@/lib/types";
import { Card } from "./ui";

// OpenFreeMap: free vector tiles, no key. Positron keeps the base quiet so the pins carry colour.
const STYLE = "https://tiles.openfreemap.org/styles/positron";
const NYC_CENTER: LngLat = [-73.94, 40.72];
// a little past the five boroughs, so panning can't wander off to New Jersey's interior
const NYC_BOUNDS: [LngLat, LngLat] = [[-74.4, 40.42], [-73.55, 41.0]];
// px kept clear around the outermost pins; the right side also clears the zoom buttons
// the expand glides the map's own element from its card slot to the case area and back
const EXPAND_MS = 560;
const EXPAND_EASE = "cubic-bezier(0.16, 1, 0.3, 1)";
const EXPAND_INSET = 16; // px kept around the expanded map inside the case area

const FIT_PADDING = { top: 28, bottom: 28, left: 28, right: 64 };
const MAX_FIT_ZOOM = 13; // one or two pins shouldn't zoom to a single block
const PIN_GAP = 18; // px: pins closer than this on screen are fanned apart

// Nudge pins that would sit on top of each other, so every case stays clickable at city zoom.
// Greedy: the selected pin keeps its true spot, each later pin steps round a ring until clear.
// The nudge is screen-only and shrinks to nothing once you zoom in far enough to separate them.
function declutter(m: MlMap, pins: Map<string, Marker>, first: string | null) {
  const order = [...pins.keys()].sort((a, b) => Number(b === first) - Number(a === first));
  const taken: { x: number; y: number }[] = [];
  for (const id of order) {
    const pin = pins.get(id)!;
    const at = m.project(pin.getLngLat());
    let dx = 0;
    let dy = 0;
    for (let step = 0; step < 18; step++) {
      if (taken.every((p) => Math.hypot(p.x - at.x - dx, p.y - at.y - dy) >= PIN_GAP)) break;
      const angle = (step * Math.PI) / 3; // six spots per ring
      const r = PIN_GAP * (1 + Math.floor(step / 6));
      dx = Math.round(Math.cos(angle) * r);
      dy = Math.round(Math.sin(angle) * r);
    }
    taken.push({ x: at.x + dx, y: at.y + dy });
    pin.setOffset([dx, dy]);
  }
}

// The basemap's own city, town and water names clip at the card's edges and sit under the pins; the
// address in the case header already says where we are.
function hidePlaceLabels(m: MlMap) {
  for (const layer of m.getStyle().layers) {
    if (layer.type === "symbol" && /^label_|^water_name/.test(layer.id)) {
      m.setLayoutProperty(layer.id, "visibility", "none");
    }
  }
}

// red is reserved for urgent and violations (DASHBOARD.md); everything else open is the accent
function tone(c: Case) {
  if (c.status === "resolved") return "done";
  return c.priority === "urgent" || c.is_violation ? "urgent" : "open";
}

export function CityMap({ cases, points, selectedId, fresh, onLine, locating, onSelect }: {
  cases: Case[];
  points: Record<string, LngLat>;
  selectedId: string | null;
  fresh: Set<string>; // just-filed cases: they ping
  onLine: Set<string>; // cases with a call live right now: they pulse
  locating: number; // live calls that haven't filed a case, so have no place yet
  onSelect: (id: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const markers = useRef(new Map<string, Marker>());
  const lib = useRef<typeof import("maplibre-gl") | null>(null);
  const select = useRef(onSelect);
  const selected = useRef(selectedId);
  const pointsRef = useRef(points);
  // the fit below waits for the style: fitting before load lands on the wrong size
  const [ready, setReady] = useState(false);
  useEffect(() => {
    select.current = onSelect;
    selected.current = selectedId;
    pointsRef.current = points;
  });

  // maplibre touches window at import, so it loads in the browser only
  useEffect(() => {
    let gone = false;
    const pins = markers.current;
    import("maplibre-gl").then((ml) => {
      if (gone || !box.current) return;
      lib.current = ml;
      map.current = new ml.Map({
        container: box.current,
        style: STYLE,
        center: NYC_CENTER,
        zoom: 9.4,
        maxBounds: NYC_BOUNDS,
        dragRotate: false,
        pitchWithRotate: false,
        attributionControl: { compact: true },
      });
      map.current.touchZoomRotate.disableRotation();
      map.current.addControl(new ml.NavigationControl({ showCompass: false }), "top-right");
      // markers placed before the map existed are drawn on the next render's effect
      const m = map.current;
      m.once("load", () => {
        m.resize();
        hidePlaceLabels(m);
        // compact attribution opens itself on load and covers a third of a small map; start folded
        box.current?.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show");
        setReady(true);
      });
      // screen distances change with zoom, so the fan-out is redone as the map moves
      m.on("zoom", () => declutter(m, markers.current, selected.current));
    });
    return () => {
      gone = true;
      pins.forEach((m) => m.remove());
      pins.clear();
      map.current?.remove();
      map.current = null;
    };
  }, []);

  // one DOM marker per placed case; the class list carries every state the CSS animates
  useEffect(() => {
    const m = map.current;
    const ml = lib.current;
    if (!m || !ml) return;
    const seen = new Set<string>();
    for (const c of cases) {
      const at = points[c.id];
      if (!at) continue;
      seen.add(c.id);
      let pin = markers.current.get(c.id);
      if (!pin) {
        const el = document.createElement("button");
        el.type = "button";
        el.addEventListener("click", () => select.current(c.id));
        pin = new ml.Marker({ element: el }).setLngLat(at).addTo(m);
        markers.current.set(c.id, pin);
      }
      pin.setLngLat(at);
      const el = pin.getElement();
      el.className = [
        "pin",
        `pin--${tone(c)}`,
        c.id === selectedId && "pin--selected",
        fresh.has(c.id) && "pin--fresh",
        onLine.has(c.id) && "pin--live",
      ].filter(Boolean).join(" ");
      el.setAttribute("aria-label", `${c.case_number}, ${COMPLAINT_LABEL[complaintOf(c)]}, ${c.address}`);
      el.title = `${c.case_number} · ${c.address}`;
      // the selected pin sits above its neighbours instead of under them
      el.style.zIndex = c.id === selectedId ? "2" : fresh.has(c.id) ? "1" : "";
    }
    for (const [id, pin] of markers.current) {
      if (!seen.has(id)) {
        pin.remove();
        markers.current.delete(id);
      }
    }
    declutter(m, markers.current, selectedId);
  });

  // Frame every placed case, not just the selected one: the whole city stays in view, the selected
  // pin is the large one, and a newly filed case widens the frame so its ping is never off-screen.
  // Keyed on the coordinates, so the 2 s poll doesn't keep re-fitting the map.
  const frame = Object.values(points).map((p) => p.join()).sort().join("|");
  useEffect(() => {
    const m = map.current;
    const ml = lib.current;
    if (!ready || !m || !ml || !frame) return;
    const bounds = new ml.LngLatBounds();
    frame.split("|").forEach((p) => bounds.extend(p.split(",").map(Number) as LngLat));
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    m.fitBounds(bounds, { padding: FIT_PADDING, maxZoom: MAX_FIT_ZOOM, duration: reduce ? 0 : 900 });
  }, [ready, frame]);

  // Expanded or not, the map is one element: it goes position:fixed at its slot's rect, then
  // animates to the case area's rect (and back), so maplibre never remounts. Its own
  // ResizeObserver redraws the tiles as the box grows.
  const slot = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [pose, setPose] = useState<CSSProperties | null>(null);
  const settle = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const rectOf = (r: DOMRect): CSSProperties => ({ top: r.top, left: r.left, width: r.width, height: r.height });
  const target = (): CSSProperties | null => {
    const area = document.querySelector("main")?.getBoundingClientRect();
    if (!area) return null;
    return {
      top: area.top + EXPAND_INSET,
      left: area.left + EXPAND_INSET,
      width: area.width - EXPAND_INSET * 2,
      height: area.height - EXPAND_INSET * 2,
    };
  };
  const motion = () =>
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "none"
      : ["top", "left", "width", "height", "border-radius", "box-shadow"].map((p) => `${p} ${EXPAND_MS}ms ${EXPAND_EASE}`).join(", ");

  // after either move, re-frame the pins for the map's new size
  const refit = useCallback(() => {
    const m = map.current;
    const ml = lib.current;
    if (!m || !ml) return;
    m.resize();
    const all = Object.values(pointsRef.current);
    if (!all.length) return;
    const bounds = new ml.LngLatBounds();
    all.forEach((p) => bounds.extend(p));
    m.fitBounds(bounds, { padding: FIT_PADDING, maxZoom: MAX_FIT_ZOOM, duration: 500 });
  }, []);

  const expand = useCallback(() => {
    const from = slot.current?.getBoundingClientRect();
    const to = target();
    if (!from || !to) return;
    clearTimeout(settle.current);
    setExpanded(true);
    setPose({ position: "fixed", ...rectOf(from), transition: "none", zIndex: 50 });
    // two frames: the browser must paint the start rect before the transition has somewhere to go from
    requestAnimationFrame(() =>
      requestAnimationFrame(() => setPose({ position: "fixed", ...to, transition: motion(), zIndex: 50, borderRadius: 12 })),
    );
    settle.current = setTimeout(refit, EXPAND_MS);
  }, [refit]);

  const collapse = useCallback(() => {
    const back = slot.current?.getBoundingClientRect();
    if (!back) return;
    clearTimeout(settle.current);
    setPose({ position: "fixed", ...rectOf(back), transition: motion(), zIndex: 50, borderRadius: 0 });
    setExpanded(false);
    settle.current = setTimeout(() => {
      setPose(null);
      refit();
    }, EXPAND_MS);
  }, [refit]);

  // Esc closes; a window resize while open re-targets the case area
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && collapse();
    const onResize = () => {
      const to = target();
      if (to) setPose((p) => ({ ...p, ...to, transition: "none" }));
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
    };
  }, [expanded, collapse]);

  useEffect(() => () => clearTimeout(settle.current), []);

  const placed = cases.filter((c) => points[c.id]).length;

  return (
    <Card
      title="City map"
      aside={
        locating > 0 ? (
          <span className="flex items-center gap-1.5 text-xs font-medium text-zinc-900">
            <span className="h-2 w-2 animate-pulse rounded-full bg-yellow-400" />
            {locating === 1 ? "1 call" : `${locating} calls`} locating…
          </span>
        ) : (
          <span className="text-xs tabular-nums text-zinc-500">{placed} of {cases.length} placed</span>
        )
      }
      flush
    >
      <div ref={slot} className="relative h-[230px] w-full">
        {/* the backdrop fades with the move; clicking it closes the map */}
        <div
          aria-hidden
          onClick={collapse}
          className={`fixed inset-0 z-40 bg-zinc-950/45 transition-opacity duration-500 ${
            expanded ? "opacity-100" : "pointer-events-none opacity-0"
          }`}
        />
        <div
          style={pose ?? undefined}
          className={`overflow-hidden bg-zinc-100 ${pose ? "" : "absolute inset-0"} ${expanded ? "shadow-[0_24px_60px_-12px_rgb(9_9_11/0.55)]" : ""}`}
          role={expanded ? "dialog" : undefined}
          aria-label={expanded ? "City map, expanded" : undefined}
        >
          <div ref={box} className="h-full w-full" />
          <button
            onClick={expanded ? collapse : expand}
            aria-label={expanded ? "Close the map" : "Expand the map"}
            className="absolute left-2 top-2 z-10 flex h-9 items-center gap-1.5 rounded-md bg-zinc-950 px-2.5 text-xs font-semibold text-white shadow-[0_2px_6px_rgb(9_9_11/0.3)] transition-colors hover:bg-zinc-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-yellow-400"
          >
            <svg viewBox="0 0 16 16" aria-hidden className="h-3.5 w-3.5">
              {expanded ? (
                <path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              ) : (
                <path d="M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5L9 7M2.5 13.5L7 9" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
              )}
            </svg>
            {expanded ? "Close" : "Expand"}
          </button>
        </div>
      </div>
      <ul className="flex gap-4 border-t border-zinc-100 px-4 py-2 text-xs text-zinc-600">
        <li className="flex items-center gap-1.5"><span className="pin-key pin--urgent" />Urgent or violation</li>
        <li className="flex items-center gap-1.5"><span className="pin-key pin--open" />Open</li>
        <li className="flex items-center gap-1.5"><span className="pin-key pin--done" />Resolved</li>
      </ul>
    </Card>
  );
}
