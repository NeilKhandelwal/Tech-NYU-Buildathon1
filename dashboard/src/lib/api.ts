import { useEffect, useRef, useState } from "react";

// Backend base URL. Defaults to the local backend; override with NEXT_PUBLIC_API_URL
// (see dashboard/.env.example). Next inlines NEXT_PUBLIC_* at build time.
export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Status = "open" | "in_progress" | "resolved";

export type Case = {
  id: string;
  name: string;
  phone: string;
  issue_type: string | null;
  description: string;
  status: Status;
  notes: string;
  created_at: string;
  updated_at: string;
};

export const STATUSES: Status[] = ["open", "in_progress", "resolved"];
export const ISSUE_TYPES = ["missed_pickup", "pothole", "streetlight", "water", "animal", "other"];

export const STATUS_COLOR: Record<Status, string> = {
  open: "bg-blue-50 text-blue-700 ring-blue-200",
  in_progress: "bg-amber-50 text-amber-700 ring-amber-200",
  resolved: "bg-emerald-50 text-emerald-700 ring-emerald-200",
};

// "missed_pickup" -> "Missed pickup"
export const humanize = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export function ago(ts: string) {
  const s = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(ts).toLocaleDateString();
}

// "m:ss" between start and end (or now, for a call still in progress)
export function duration(start: string, end: string | null) {
  const s = Math.max(0, ((end ? new Date(end).getTime() : Date.now()) - new Date(start).getTime()) / 1000);
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

export const BACKEND_DOWN = `Backend unreachable at ${API}`;

// Who is on the keyboard, for the audit log. Nav sets it from the session; when nobody is
// signed in (auth disabled in dev) the header is simply left off. The backend starts
// recording X-Actor in a later PR and ignores it until then, so this is safe to send now.
let actor: string | null = null;
export const setActor = (name: string | null) => {
  actor = name;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    const headers = { ...init?.headers, ...(actor ? { "X-Actor": actor } : {}) };
    res = await fetch(API + path, { cache: "no-store", ...init, headers });
  } catch {
    throw new Error(BACKEND_DOWN);
  }
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const listCases = () => request<Case[]>("/cases");
export const getCase = (id: string) => request<Case>(`/cases/${id}`);
// notes is deliberately not patchable: the backend answers 422 (CONTRACT "## Data layer").
export const patchCase = (id: string, body: Partial<Pick<Case, "status" | "issue_type" | "description">>) =>
  request<Case>(`/cases/${id}`, json("PATCH", body));
// One POST appends one note, so two people writing in the same second both keep theirs.
export const addNote = (id: string, text: string) =>
  request<CaseEvent>(`/cases/${id}/notes`, json("POST", { text }));

// ---- Stretch: calls + transcripts (see ../CONTRACT.md "## Stretch") ----

export type Call = {
  id: string;
  case_id: string | null; // the case the agent is working right now (a cursor, not the whole story)
  case_ids: string[]; // every case this call created or found, in link order
  status: "active" | "needs_person" | "ended";
  started_at: string;
  ended_at: string | null;
  summary?: string | null; // written by the agent on hang-up (CONTRACT "## Summary")
  room: string | null;
  transfer_reason: string | null; // why the agent handed off (CONTRACT "## Warm transfer")
};

export type TranscriptLine = {
  id: number;
  call_id: string;
  role: "user" | "agent";
  text: string;
  ts: string;
};

export type CallWithTranscript = Call & { transcript: TranscriptLine[] };

// Every case a call touched. Falls back to the cursor so a call fetched before
// call_cases existed still renders.
export const caseIdsOf = (c: Pick<Call, "case_id" | "case_ids">) =>
  c.case_ids?.length ? c.case_ids : c.case_id ? [c.case_id] : [];

export const listCalls = (status?: Call["status"]) =>
  request<Call[]>(status ? `/calls?status=${status}` : "/calls");
export const getCall = (id: string) => request<CallWithTranscript>(`/calls/${id}`);
export const getCaseCalls = (id: string) => request<CallWithTranscript[]>(`/cases/${id}/calls`);

// Staff-side writes on a call: picking up a transfer, ending it, linking a case.
export const patchCall = (id: string, body: Partial<Pick<Call, "status" | "transfer_reason" | "case_id">>) =>
  request<Call>(`/calls/${id}`, json("PATCH", body));

// ---- Containment (see ../CONTRACT.md "## Containment") ----

export type Stats = {
  calls: number; // contained + needs_person + active
  contained: number; // ended, and nobody ever asked for a person
  needs_person: number; // waiting for staff, or ended after a transfer
  active: number; // still on the line, so not scored either way
  containment: number | null; // 0-1; null when nothing in the window has been handled yet
};

// since/until are on the call's started_at, both inclusive, both optional.
export const getStats = (since?: string) =>
  request<Stats>(since ? `/stats?since=${encodeURIComponent(since)}` : "/stats");

// ---- Browser call (see ../CONTRACT.md "## Browser call") ----

export type Grant = { token: string; url: string; room: string };

export const getToken = (identity: string) =>
  request<Grant>(`/token?identity=${encodeURIComponent(identity)}`);

export const listCallsByRoom = (room: string) =>
  request<Call[]>(`/calls?room=${encodeURIComponent(room)}`);

// ---- Audit log (see ../CONTRACT.md "## Audit") ----

export type CaseEvent = {
  id: number;
  case_id: string;
  // "note" is the note itself: its text is new_value, and it carries its own source and ts
  field: "created" | "status" | "note" | "issue_type" | "description" | "call_linked" | "looked_up";
  old_value: string | null;
  new_value: string | null;
  source: "voice" | "staff"; // which system wrote it
  actor: string | null; // which person, from X-Actor; null on a voice call and on older rows
  ts: string;
};

export const getCaseEvents = (id: string) => request<CaseEvent[]>(`/cases/${id}/events`);

// Poll only when the socket is down, and only if the caller asked for one (pollMs: 0
// means "never poll" — Nav wants the dot, not the traffic). Pure so the rule is testable
// without a browser.
export const shouldPoll = (connected: boolean, pollMs: number) => !connected && pollMs > 0;

// ---- The one socket ----
// Every useLiveRefresh on the page shares a single WS /ws connection: the page's hook and
// Nav's Live/Polling dot used to open one each. Ref-counted — the first subscriber opens
// it, the last one to leave closes it — so nothing here outlives the pages using it.
type Subscriber = {
  frame: () => void; // "something changed": refetch
  state: (connected: boolean) => void;
};

const subscribers = new Set<Subscriber>();
let socket: WebSocket | null = null;
let socketRetry: ReturnType<typeof setTimeout> | undefined;
let socketUp = false;

function announce(up: boolean) {
  socketUp = up;
  subscribers.forEach((s) => s.state(up));
}

function openSocket() {
  const ws = new WebSocket(API.replace(/^http/, "ws") + "/ws");
  socket = ws;
  // A socket we have already replaced or dropped must not announce or reconnect: its
  // close event lands after the new one is up (React remounts do exactly this).
  const current = () => socket === ws;
  ws.onopen = () => {
    if (!current()) return;
    announce(true);
    // Catch up on everything that changed while the socket was down.
    subscribers.forEach((s) => s.frame());
  };
  ws.onmessage = () => {
    if (current()) subscribers.forEach((s) => s.frame());
  };
  // No onerror handler: the spec always follows an error with a close, which is here.
  ws.onclose = () => {
    if (!current()) return;
    announce(false);
    if (subscribers.size) socketRetry = setTimeout(openSocket, 2000);
  };
}

function subscribe(sub: Subscriber) {
  subscribers.add(sub);
  if (socket) sub.state(socketUp); // joined a socket that is already open: tell it so
  else openSocket();
  return () => {
    subscribers.delete(sub);
    if (subscribers.size) return;
    clearTimeout(socketRetry);
    const dropped = socket;
    socket = null; // before close(), so the close event knows it is stale
    socketUp = false;
    dropped?.close();
  };
}

// Refetches on every frame from the shared socket (frames carry no payload, they just mean
// "something changed"), once on mount, and once on every socket open so a reconnect catches
// up on what it missed. The pollMs interval is the fallback: it exists only while the socket
// is down. Returns whether the socket is up.
export function useLiveRefresh(refetch: () => void, { pollMs = 2000 }: { pollMs?: number } = {}) {
  const latest = useRef(refetch);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    latest.current = refetch;
  });
  // What each page used to do itself: fetch on mount, and again whenever refetch changes
  // identity (a new :id in the route, an edit that stops being dirty).
  useEffect(() => {
    refetch();
  }, [refetch]);
  useEffect(() => subscribe({ frame: () => latest.current(), state: setConnected }), []);
  useEffect(() => {
    if (!shouldPoll(connected, pollMs)) return;
    const poll = setInterval(() => latest.current(), pollMs);
    return () => clearInterval(poll);
  }, [connected, pollMs]);
  return connected;
}

// Change tracking so the UI can flash fields the agent just updated. Pure: use it
// inside a functional setState. Keys stay "changed" for ~2.5s across refetches.
export type Tracked<T> = { data: T | null; changed: Set<string>; at: number };
export const untracked = <T,>(): Tracked<T> => ({ data: null, changed: new Set(), at: 0 });

export function track<T extends object>(prev: Tracked<T>, data: T): Tracked<T> {
  const before = prev.data as Record<string, unknown> | null;
  const keys = before
    ? Object.keys(data).filter((k) => k !== "updated_at" && (data as Record<string, unknown>)[k] !== before[k])
    : [];
  return { data, ...since(prev, keys) };
}

// Shared by both trackers: a fresh change wins, otherwise the last set stays alive ~2.5s
// so the flash survives the refetches that land during it.
function since(prev: { changed: Set<string>; at: number }, keys: string[]) {
  if (keys.length) return { changed: new Set(keys), at: Date.now() };
  const fresh = Date.now() - prev.at < 2500;
  return { changed: fresh ? prev.changed : new Set<string>(), at: prev.at };
}

// Same idea one level up: which rows in a list are new or have a newer updated_at.
// changed holds row ids. Nothing flashes on the first load.
export type TrackedList<T> = { data: T[]; changed: Set<string>; at: number };
export const untrackedList = <T,>(): TrackedList<T> => ({ data: [], changed: new Set(), at: 0 });

export function trackList<T extends { id: string; updated_at: string }>(
  prev: TrackedList<T>,
  data: T[],
): TrackedList<T> {
  const before = new Map(prev.data.map((r) => [r.id, r.updated_at]));
  const keys = before.size ? data.filter((r) => before.get(r.id) !== r.updated_at).map((r) => r.id) : [];
  return { data, ...since(prev, keys) };
}

export const flash = (t: Tracked<unknown>, key: string) =>
  `rounded px-1 -mx-1 transition-colors duration-700 ${t.changed.has(key) ? "bg-amber-100 ring-1 ring-amber-300" : ""}`;

// Re-renders the caller once a second so elapsed-time labels (duration()) keep ticking
// between fetches. Returns the tick in case a caller wants the timestamp itself.
export function useNow(ms = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(timer);
  }, [ms]);
  return now;
}
