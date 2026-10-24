import type { CaseEvent } from "./types";

export type CallState = "idle" | "calling" | "answered" | "done" | "no_answer";

const STALE_MS = 15 * 60_000;

// The button's state comes from the data, not from local clicks, so a reload (or a second
// screen) shows the same thing. Only events after the latest saved action count: each
// action + callback is a new attempt.
//   POST /callback logs callback_call {conversation_id}, no text -> calling
//   record_resident_response logs resident_response             -> answered
//   the post-call webhook logs callback_call with the transcript -> done (or no_answer)
export function callState(events: CaseEvent[]): CallState {
  const lastAction = events.findLast((e) => e.type === "caseworker_action");
  if (!lastAction) return "idle";
  const after = events.filter((e) => e.created_at >= lastAction.created_at);
  const dialed = after.some((e) => e.type === "callback_call");
  const answered = after.some((e) => e.type === "resident_response");
  // the webhook marks its event stage "ended"; a no-pickup call can end with an empty transcript
  const ended = after.some(
    (e) => e.type === "callback_call" && (e.payload?.stage === "ended" || e.text_original),
  );
  if (ended) return answered ? "done" : "no_answer";
  if (answered) {
    // a call is over within minutes; an answer this old whose post-call webhook never came
    // (seed data, a lost webhook) must not lock the button forever
    const at = Date.parse(after.findLast((e) => e.type === "resident_response")!.created_at);
    return Date.now() - at > STALE_MS ? "done" : "answered";
  }
  return dialed ? "calling" : "idle";
}
