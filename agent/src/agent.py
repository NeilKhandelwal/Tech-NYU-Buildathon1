import asyncio
import logging
import os
import re
import textwrap

import httpx
from dotenv import load_dotenv
from livekit import api
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ConversationItemAddedEvent,
    JobContext,
    RunContext,
    TurnHandlingOptions,
    cli,
    function_tool,
    get_job_context,
    inference,
    room_io,
)
from livekit.agents.llm import ChatContext, ChatMessage
from livekit.plugins import ai_coustics

logger = logging.getLogger("agent")

load_dotenv(".env")

BACKEND = os.environ.get("BACKEND_URL", "http://localhost:8000")
BACKEND_DOWN = "I couldn't reach the case system right now, please try again later."
ISSUE_TYPES = "missed_pickup, pothole, streetlight, water, animal, other"
HEADERS = {"X-Source": "voice"}  # audit log attributes these writes to the voice agent
SUMMARY_PROMPT = (
    "Summarize this City services call for staff in at most two sentences: what the "
    "resident wanted, what was actually done (case created/found, note added), and any "
    "follow-up needed; list every case created or found. State only what the transcript "
    "shows; if the call ended before a case was created or found, say so plainly. "
    "Transcript:\n"
)


def digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def valid_phone(phone: str) -> bool:
    # STT sometimes splits a number across turns ("25" + "7062"); refuse to file a partial one
    return len(digits(phone)) == 10


def normalize_code(spoken: str) -> str:
    # "Blue River, Maple" and "blue and river dash maple" are both blue-river-maple
    # (same rule as the backend's codes.normalize; the backend is still the one that matches)
    words = [w for w in re.split(r"[\s,-]+", spoken.strip().lower()) if w and w not in ("and", "dash")]
    return "-".join(words)


def is_filed(classified: bool, described: bool) -> bool:
    # a case is "filed" once it has an issue type and a description (that's when its lookup
    # code is read back); from then on it is read-only for update_case
    return classified and described


def can_open_case(current: str | None, classified: bool, described: bool) -> bool:
    # one problem per case, but a caller may raise two: allow a second case only once the
    # first is filed, so the agent can't leave a half-filled case behind
    return current is None or is_filed(classified, described)


def clean_text(text: str) -> str:
    # expressive TTS mode embeds <expr .../> tags in agent text; the dashboard shouldn't see them
    return re.sub(r"\s+", " ", re.sub(r"<expr[^>]*/>", "", text)).strip()


# Call-record helpers. call_id is None when the backend was down at session start:
# every helper then no-ops, so the voice call keeps working without the dashboard.
async def start_call(room: str | None = None) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=5, headers=HEADERS) as client:
            r = await client.post(f"{BACKEND}/calls", json={"room": room})
            r.raise_for_status()
            return r.json()["id"]
    except Exception:
        logger.warning("could not create call record; transcript will not be streamed")
        return None


async def post_line(call_id: str | None, role: str, text: str) -> None:
    if call_id is None or not text:
        return
    try:
        async with httpx.AsyncClient(timeout=5, headers=HEADERS) as client:
            r = await client.post(
                f"{BACKEND}/calls/{call_id}/transcript", json={"role": role, "text": text}
            )
            r.raise_for_status()
    except Exception:
        logger.warning("post_line failed", exc_info=True)


async def patch_call(call_id: str | None, body: dict) -> bool:
    if call_id is None:
        return False
    try:
        async with httpx.AsyncClient(timeout=5, headers=HEADERS) as client:
            r = await client.patch(f"{BACKEND}/calls/{call_id}", json=body)
            r.raise_for_status()
            return True
    except Exception:
        logger.warning("patch_call failed", exc_info=True)
        return False


async def link_call_case(call_id: str | None, case_id: str, how: str) -> bool:
    # how is 'created' or 'looked_up'; the backend keeps one link per (call, case)
    if call_id is None:
        return False
    try:
        async with httpx.AsyncClient(timeout=5, headers=HEADERS) as client:
            r = await client.post(f"{BACKEND}/calls/{call_id}/cases",
                                  json={"case_id": case_id, "how": how})
            r.raise_for_status()
            return True
    except Exception:
        logger.warning("link_call_case failed", exc_info=True)
        return False


async def end_call(call_id: str | None) -> None:
    await patch_call(call_id, {"status": "ended"})


def history_text(items) -> str:
    # the ChatContext also holds tool calls and their outputs; only spoken turns summarize well
    turns = []
    for item in items:
        if isinstance(item, ChatMessage) and item.role in ("user", "assistant"):
            text = clean_text(item.text_content or "")
            if text:
                turns.append(f"{'Caller' if item.role == 'user' else 'Agent'}: {text}")
    return "\n".join(turns) if len(turns) >= 2 else ""  # one turn is just the greeting


async def summarize(session: AgentSession) -> str | None:
    transcript = history_text(session.history.items)
    if not transcript:
        return None

    async def run() -> str:
        llm = inference.LLM(model="openai/gpt-4.1-mini")
        chat_ctx = ChatContext.empty()
        chat_ctx.add_message(role="user", content=SUMMARY_PROMPT + transcript)
        parts = []
        async with llm.chat(chat_ctx=chat_ctx) as stream:
            async for chunk in stream:
                if chunk.delta and chunk.delta.content:
                    parts.append(chunk.delta.content)
        return "".join(parts).strip()

    try:
        return await asyncio.wait_for(run(), timeout=15) or None
    except Exception:  # a missing summary must never keep the call from being marked ended
        logger.warning("summarize failed", exc_info=True)
        return None


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            # LiveKit Inference model (no provider key needed). Starter default is
            # google/gemma-4-31b-it; gpt-4.1-mini chosen for reliable tool calling.
            llm=inference.LLM(model="openai/gpt-4.1-mini"),
            instructions=textwrap.dedent(
                f"""\
                You are the City services phone line. You only do three things: file a new
                service request, look up an existing case from its lookup code, or add a note to
                a case. Politely decline anything else.

                To file a request, collect one at a time: the caller's name, then their phone
                number. If the caller spells their name letter by letter, use exactly the
                spelled version and drop what you heard before. Only the phone number needs
                confirming: read the digits back. As soon as the phone number is confirmed,
                call create_case with the name and phone, then ask what the issue is. Do not
                mention the case ID yet. The moment you can map what the caller says onto
                exactly one of {ISSUE_TYPES}, call update_case with that issue_type, then ask
                for a one-sentence description and call update_case with the description. Do
                not ask the caller to confirm the name, issue type, or description; just move
                on. Only after the description is saved, read the case ID back slowly,
                character by character, then say "Your lookup code is <word>, <word>, <word>.
                Say it on your next call to check on this case." Read the three words with a
                comma between them so they land one at a time, and offer once to repeat them.
                Never say the lookup code before the description has been saved. One problem
                per case: if the caller raises a second, separate problem, finish the first
                (issue type and description), then call create_case again — never update_case;
                a filed case is not changed. A different problem is a new case: call create_case
                again with the same name and phone, don't ask for them again, then classify it and
                take its description; the same problem said again is not a new case. Each case has
                its own ID and its own lookup code, read back after that case's description.

                If the caller asks about an existing case, ask for their lookup code (three
                words) and call lookup_case. Do not look up cases by phone number or case ID.
                If three codes in a row do not match, call transfer_to_staff with the reason
                "could not verify lookup code". To add a note, call add_note with just the
                note's wording; it goes on the case found or opened on this call, so look the
                case up first and don't ask for the note again. When you find a case, always
                tell the caller its current status in plain words (open, in progress, or
                resolved) before anything else about it.

                This is a voice call: plain text only, one or two short sentences per reply,
                spell out numbers. Never invent a case status or ID; only repeat what a tool
                returned. If a tool says the system is unreachable, tell the caller that. If asked why
                you need something, say you need it to file or find the case; don't cite policies.
                If the caller asks for a human, is upset, or wants something none of your tools
                cover, call transfer_to_staff with a short reason, then tell them someone is
                picking up and keep the line open. When the caller says they have nothing else,
                say a short goodbye such as "Have a great day" and then call end_call. Never
                call end_call after transfer_to_staff.
                """
            ),
        )
        self.call_id: str | None = None  # set by the entrypoint once POST /calls succeeds
        # the case being worked right now (update_case acts on it, so the LLM never has to
        # repeat the ID) and every case this call touched, in order
        self.current_case: str | None = None
        self.cases: list[str] = []
        self.current_classified = False  # current_case has an issue_type
        self.current_described = False  # current_case has a description; both gate a second case
        self.lookup_code: str | None = None  # current_case's code, spoken once its description saves
        self.link_how = "created"
        self.call_linked = False  # current_case is linked to the call record; retried until it is

    async def _link_call(self) -> None:
        # idempotent: a failed link right after create_case is retried on the next tool call,
        # so a transient backend error can't leave the case detached from its call for good
        if self.current_case and not self.call_linked:
            self.call_linked = await link_call_case(self.call_id, self.current_case, self.link_how)

    @function_tool
    async def create_case(self, context: RunContext, name: str, phone: str) -> str:
        """Open a new service request. Call as soon as you have the caller's name and their
        confirmed phone number, before asking about the issue.

        Args:
            name: caller's full name
            phone: caller's phone number
        """
        if not can_open_case(self.current_case, self.current_classified, self.current_described):
            await self._link_call()
            return "Finish the current case first (issue type and description), then open another."
        if not valid_phone(phone):
            n = len(digits(phone))
            return f"That number has {n} digits; a phone number should have ten. Could you say it again?"
        # issue_type/description are filled in by update_case as the caller explains
        body = {"name": name, "phone": digits(phone), "issue_type": None, "description": ""}
        try:
            async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
                r = await client.post(f"{BACKEND}/cases", json=body)
                r.raise_for_status()
                created = r.json()
                case_id = created["id"]
                self.lookup_code = created["lookup_code"]  # the only response that carries it
        except Exception:  # never raise: the agent must say the failure, not crash
            logger.exception("create_case failed")
            return BACKEND_DOWN
        self.current_case = case_id
        self.cases.append(case_id)
        self.current_classified = self.current_described = False
        self.link_how, self.call_linked = "created", False
        await self._link_call()
        return f"Started case {case_id}"

    @function_tool
    async def update_case(
        self,
        context: RunContext,
        issue_type: str | None = None,
        description: str | None = None,
    ) -> str:
        """Fill in the issue type or description on the case opened by create_case.
        Call it as soon as you know each one; you can call it twice.

        Args:
            issue_type: exactly one of missed_pickup, pothole, streetlight, water, animal, other
            description: one-sentence description of the problem
        """
        if not self.current_case:
            return "No case is open yet; call create_case with the name and phone first."
        if is_filed(self.current_classified, self.current_described):
            # a filed case is never rewritten: "my trash wasn't collected" after a pothole was
            # filed is a NEW case, not a correction to the old one
            return (
                f"Case {self.current_case} is already filed and can't be changed. If this is a "
                "different problem, call create_case to open a new case. If it's a correction "
                "to the same problem, call add_note."
            )
        body = {}
        if issue_type is not None:
            if issue_type not in ISSUE_TYPES.split(", "):
                return f"issue_type must be exactly one of: {ISSUE_TYPES}"
            body["issue_type"] = issue_type
        if description is not None:
            body["description"] = description
        if not body:
            return "Nothing to update."
        try:
            async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
                r = await client.patch(f"{BACKEND}/cases/{self.current_case}", json=body)
                r.raise_for_status()
        except Exception:  # never raise: the agent must say the failure, not crash
            logger.exception("update_case failed")
            return BACKEND_DOWN
        if "issue_type" in body:
            self.current_classified = True
        if "description" in body:
            self.current_described = True  # classified + described = filed; a second case may open
        await self._link_call()
        if description is not None and self.lookup_code:
            # withheld until now so the agent can't read it out before the case is filled in
            return f"Updated case {self.current_case}. Lookup code {self.lookup_code}"
        return f"Updated case {self.current_case}"

    @function_tool
    async def lookup_case(self, context: RunContext, code: str) -> str:
        """Find the caller's case from the three-word lookup code they were given when it was
        filed. This is the only way to reach an existing case; a phone number or case ID is not
        enough.

        Args:
            code: the three words the caller says, like "blue river maple"
        """
        headers = {**HEADERS, "X-Call-Id": self.call_id or ""}  # backend counts wrong codes per call
        try:
            async with httpx.AsyncClient(timeout=10, headers=headers) as client:
                r = await client.get(f"{BACKEND}/cases/lookup", params={"code": normalize_code(code)})
                if r.status_code == 404:
                    return "No case matches that code."
                if r.status_code == 429:
                    return "Too many attempts."
                r.raise_for_status()
                c = r.json()
        except Exception:  # never raise: the agent must say the failure, not crash
            logger.exception("lookup_case failed")
            return BACKEND_DOWN
        # work the case it found from here on: add_note writes to it, and an existing case is
        # not one the agent is still filling in, so it never blocks a new case
        # an existing case counts as filed: update_case must never rewrite it
        self.current_case, self.link_how = c["id"], "looked_up"
        self.current_classified = self.current_described = True
        self.lookup_code = None  # not ours to read out: the caller already has this one's code
        self.call_linked = False
        if c["id"] not in self.cases:
            self.cases.append(c["id"])
        await self._link_call()
        issue = c["issue_type"] or "not yet classified"
        # no filed-date here: it's read aloud on every lookup
        return f"Case {c['id']}, {issue}, status {c['status']}, description {c['description']}"

    @function_tool
    async def add_note(self, context: RunContext, note: str) -> str:
        """Append a note to the case opened or looked up on this call.

        Args:
            note: the note to add
        """
        if not self.current_case:
            return "No case on this call yet; ask for the three-word lookup code and call lookup_case."
        try:
            async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
                # one POST, no read-modify-write: two writers in the same second both keep
                # their note, and the audit log records the note rather than a rewrite
                r = await client.post(
                    f"{BACKEND}/cases/{self.current_case}/notes", json={"text": note}
                )
                r.raise_for_status()
                return f"Note added to case {self.current_case}"
        except Exception:
            logger.exception("add_note failed")
            return BACKEND_DOWN

    @function_tool
    async def transfer_to_staff(self, context: RunContext, reason: str) -> str:
        """Hand the call to a human staff member. Call it when the caller asks for a person,
        is upset, or wants something the other tools don't cover.

        Args:
            reason: one short phrase saying why, shown to staff on the dashboard
        """
        # marks the call needs_person; it stays live (no ended_at) so staff see it waiting
        await patch_call(self.call_id, {"status": "needs_person", "transfer_reason": reason})
        return ("Transfer requested. Tell the caller a staff member will pick up shortly and to "
                "stay on the line. Do not end the call.")

    @function_tool
    async def end_call(self, context: RunContext) -> str:
        """End the call. Call this only after you have said goodbye and the caller has nothing
        else."""
        await context.wait_for_playout()  # the goodbye is still playing; let the caller hear it
        await asyncio.sleep(2.5)
        # deleting the room disconnects the browser, which triggers the existing shutdown
        # callback (summary + status=ended) -- do NOT call end_call()/patch_call here or the
        # call would be marked ended twice
        try:
            job = get_job_context()
            await job.api.room.delete_room(api.DeleteRoomRequest(room=job.room.name))
        except Exception:  # never raise: the agent must say the failure, not crash
            logger.exception("end_call failed")
            return "I couldn't hang up; ask the caller to hang up on their end."
        return "Call ended."


server = AgentServer()


# no agent_name: the worker is auto-dispatched to every new room, so the browser
# client only needs a token (explicit dispatch would need a second API call)
@server.rtc_session()
async def city_services(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}

    # Voice pipeline as shipped by the starter: AssemblyAI STT, Fish Audio TTS,
    # LiveKit turn detector (supplies VAD), all via LiveKit Inference.
    session = AgentSession(
        stt=inference.STT(model="assemblyai/universal-3-5-pro", language="en"),
        tts=inference.TTS(
            model="fishaudio/s2.1-pro", voice="fa4c9eb3dccc4806b382b40d61c6b10a"
        ),
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
            interruption={"mode": "adaptive"},
            preemptive_generation={"enabled": True},
        ),
        expressive=True,
    )

    agent = Assistant()
    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(
                    model=ai_coustics.EnhancerModel.QUAIL_VF_S
                ),
            ),
        ),
    )

    agent.call_id = await start_call(ctx.room.name)

    # session.on handlers must be sync, so HTTP work runs in a task; the set keeps a
    # strong reference until done (asyncio only holds tasks weakly, so they could be GC'd)
    tasks: set[asyncio.Task] = set()

    def spawn(coro):
        t = asyncio.create_task(coro)
        tasks.add(t)
        t.add_done_callback(tasks.discard)

    @session.on("conversation_item_added")
    def _on_item(ev: ConversationItemAddedEvent):
        # one line per committed turn for both roles (per-segment STT events would
        # split "925." / "915-7062." into separate lines); assistant items arrive after playout
        if isinstance(ev.item, ChatMessage) and ev.item.role in ("user", "assistant"):
            role = "user" if ev.item.role == "user" else "agent"
            spawn(post_line(agent.call_id, role, clean_text(ev.item.text_content or "")))

    # shutdown callbacks are awaited by the job runner (a session "close" handler isn't), so the
    # process waits for the summary and the call is reliably marked ended even on Ctrl-C;
    # end_call always runs, even when summarize fails
    async def on_shutdown():
        summary = await summarize(session)
        if summary:
            await patch_call(agent.call_id, {"summary": summary})
        await end_call(agent.call_id)

    ctx.add_shutdown_callback(on_shutdown)

    await ctx.connect()

    await session.generate_reply(
        instructions="Greet the caller as the City services line and ask how you can help."
    )


if __name__ == "__main__":
    cli.run_app(server)
