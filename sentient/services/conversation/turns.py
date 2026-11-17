"""Turn ownership for sc-brain: ids, cancellation, preemption, and deadlines.

The LLM is called only through the contemplation adapter's public methods
``chat_stream`` and ``generate``. WS4 may change the adapter's internals;
this module depends on that call signature and nothing else.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from sentient.services.conversation.contract import format_ts, local_time_phrase

logger = logging.getLogger("sentient.sc-brain")

Publisher = Callable[[str, dict[str, Any]], Awaitable[None]]

QUEUE_FAILURE = {
    "voice": "Sorry, I missed that.",
    "chat": "I couldn't start on that in time. Please try again.",
    "background": "That background request expired before it started.",
}
GENERATION_FAILURE = {
    "voice": "Sorry, that took too long.",
    "chat": "Sorry, I ran out of time.",
    "background": "That background request ran out of time.",
}
ERROR_TEXT = {
    "voice": "Sorry, I couldn't think of a reply.",
    "chat": "I hit an error while thinking. Please try again.",
    "background": "That background request failed.",
}
STOP_SEQUENCES = ["Jack:", "\n\nJack", "\nJack:"]
TERMINAL = {"completed", "cancelled", "failed"}


@dataclass
class TurnLimits:
    """Deadlines from the voice-turn rules. Tests pass shorter copies."""

    queue_s: dict[str, float]
    generation_s: dict[str, float]
    max_tokens: dict[str, int]

    @classmethod
    def standard(cls) -> "TurnLimits":
        return cls(
            queue_s={"voice": 2.0, "chat": 30.0, "background": 3600.0},
            generation_s={"voice": 20.0, "chat": 60.0, "background": 180.0},
            max_tokens={"voice": 256, "chat": 2048, "background": 2048},
        )


@dataclass
class Turn:
    turn_id: str
    kind: str
    text: str
    source: str
    user: str
    enqueued_at: float
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    status: str = "queued"
    disposition: Optional[str] = None
    cancel_reason: str = "client"
    attempts: int = 0
    started_at: Optional[float] = None
    playback: Optional[str] = None

    def age(self, now: float) -> float:
        return now - self.enqueued_at


class TurnEngine:
    """Single worker. A voice turn cancels an in-flight non-voice generation and requeues it."""

    def __init__(
        self,
        llm: Any,
        publisher: Publisher,
        *,
        limits: Optional[TurnLimits] = None,
        model: str = "local",
        source: str = "sc-brain",
        clock: Callable[[], float] = time.monotonic,
    ):
        self.llm = llm
        self.publisher = publisher
        self.limits = limits or TurnLimits.standard()
        self.model = model
        self.source = source
        self._clock = clock
        self._queue: deque[Turn] = deque()
        self._turns: dict[str, Turn] = {}
        self._history: dict[str, list[dict[str, str]]] = {}
        self._open_voice: dict[str, str] = {}
        self._active: Optional[Turn] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._tick = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def active(self) -> Optional[Turn]:
        return self._active

    def get(self, turn_id: str) -> Optional[Turn]:
        return self._turns.get(turn_id)

    async def start(self) -> None:
        if self._task is None:
            self._running = True
            self._task = asyncio.create_task(self._loop(), name="sc-brain-turns")

    async def stop(self) -> None:
        self._running = False
        if self._active is not None and self._active.status == "running":
            self._active.disposition = "cancel"
            self._active.cancel_reason = "client"
            self._active.cancel_event.set()
        self._tick.set()
        task = self._task
        self._task = None
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def handle_wake(self, payload: dict[str, Any]) -> str:
        turn_id = _turn_id(payload) or str(uuid.uuid4())
        source = str(payload.get("source") or "sc-speech")
        self._open_voice[source] = turn_id
        self._log(turn_id, "wake", source)
        await self._avatar(turn_id, "listening", payload.get("emotion"))
        return turn_id

    async def handle_transcript(self, payload: dict[str, Any]) -> Optional[Turn]:
        text = str(payload.get("text") or "").strip()
        if not text:
            logger.info("ignoring empty transcript")
            return None
        source = str(payload.get("source") or "sc-speech")
        turn_id = _turn_id(payload) or self._open_voice.get(source) or str(uuid.uuid4())
        return await self.submit(
            turn_id=turn_id,
            kind="voice",
            text=text,
            source=source,
            user=str(payload.get("user") or "jack"),
        )

    async def handle_chat_input(self, payload: dict[str, Any]) -> Optional[Turn]:
        text = str(payload.get("text") or "").strip()
        if not text:
            logger.info("ignoring empty chat input")
            return None
        kind = str(payload.get("kind") or "chat")
        if kind not in self.limits.queue_s:
            kind = "chat"
        source = str(payload.get("source") or "sc-gateway")
        turn_id = _turn_id(payload) or str(uuid.uuid4())
        return await self.submit(
            turn_id=turn_id,
            kind=kind,
            text=text,
            source=source,
            user=str(payload.get("user") or "jack"),
        )

    async def handle_cancel(self, payload: dict[str, Any]) -> None:
        reason = str(payload.get("reason") or "client")
        if reason not in ("barge-in", "superseded", "deadline", "client"):
            reason = "client"
        requested = str(payload.get("turn_id") or "*")
        turn = self._resolve(requested)
        label = turn.turn_id if turn else requested
        self._log(label, "cancel", str(payload.get("source") or "unknown"))
        if turn is None or turn.status in TERMINAL:
            logger.info("turn %s cancel ignored", label)
            return
        turn.cancel_reason = reason
        turn.disposition = "cancel"
        if turn.status == "queued":
            self._queue = deque(item for item in self._queue if item.turn_id != turn.turn_id)
            turn.status = "cancelled"
            await self._finish_cancelled(turn)
            return
        turn.cancel_event.set()

    async def handle_tts_state(self, payload: dict[str, Any]) -> None:
        turn_id = _turn_id(payload)
        state = str(payload.get("state") or "")
        self._log(turn_id or "-", "playback", str(payload.get("source") or "sc-speech"))
        if not turn_id or turn_id == "*":
            return
        turn = self._turns.get(turn_id)
        if turn is None:
            return
        turn.playback = state
        if state in ("completed", "stopped", "failed"):
            await self._avatar(turn.turn_id, "idle", None)

    async def submit(self, *, turn_id: str, kind: str, text: str, source: str, user: str) -> Optional[Turn]:
        existing = self._turns.get(turn_id)
        if existing is not None and existing.status not in TERMINAL:
            logger.info("turn %s duplicate input ignored (%s)", turn_id, existing.status)
            return existing
        turn = Turn(
            turn_id=turn_id,
            kind=kind,
            text=text,
            source=source,
            user=user,
            enqueued_at=self._clock(),
        )
        self._remember(turn)
        if kind == "voice":
            self._preempt_non_voice()
            self._insert_voice(turn)
        else:
            self._queue.append(turn)
        self._log(turn.turn_id, "queued", source)
        self._tick.set()
        return turn

    async def wait_status(self, turn_id: str, statuses: set[str], timeout: float = 2.0) -> Turn:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            turn = self._turns.get(turn_id)
            if turn is not None and turn.status in statuses:
                return turn
            await asyncio.sleep(0.01)
        current = self._turns.get(turn_id)
        raise TimeoutError(f"turn {turn_id} status {getattr(current, 'status', None)}")

    def _resolve(self, turn_id: str) -> Optional[Turn]:
        if turn_id != "*":
            return self._turns.get(turn_id)
        live = [
            turn for turn in self._turns.values()
            if turn.kind == "voice" and turn.status in ("queued", "running")
        ]
        if not live:
            return None
        return max(live, key=lambda turn: turn.enqueued_at)

    def _remember(self, turn: Turn) -> None:
        self._turns[turn.turn_id] = turn
        if len(self._turns) <= 200:
            return
        finished = [item for item in self._turns.values() if item.status in TERMINAL]
        finished.sort(key=lambda item: item.enqueued_at)
        for old in finished[: len(self._turns) - 200]:
            del self._turns[old.turn_id]

    def _preempt_non_voice(self) -> None:
        active = self._active
        if active is None or active.kind == "voice" or active.status != "running":
            return
        if active.disposition == "cancel":
            return
        active.disposition = "requeue"
        active.cancel_event.set()
        self._log(active.turn_id, "preempted", self.source)

    def _insert_voice(self, turn: Turn) -> None:
        index = 0
        for existing in self._queue:
            if existing.kind != "voice":
                break
            index += 1
        self._queue.insert(index, turn)

    async def _loop(self) -> None:
        try:
            while self._running:
                if not self._queue:
                    self._idle.set()
                    if self._tick.is_set():
                        self._tick.clear()
                        continue
                    await self._tick.wait()
                    continue
                self._idle.clear()
                turn = self._queue.popleft()
                if turn.status != "queued":
                    continue
                await self._run_turn(turn)
        except asyncio.CancelledError:
            raise
        finally:
            self._idle.set()

    async def _run_turn(self, turn: Turn) -> None:
        waited = turn.age(self._clock())
        limit = self.limits.queue_s[turn.kind]
        if waited > limit:
            await self._fail_queue(turn, waited, limit)
            return
        self._active = turn
        turn.status = "running"
        turn.started_at = self._clock()
        self._log(turn.turn_id, "generation_start", self.source)
        await self._avatar(turn.turn_id, "thinking", None)
        try:
            await self._generate(turn)
        finally:
            if self._active is turn:
                self._active = None
        if turn.disposition == "requeue":
            self._requeue(turn)

    def _requeue(self, turn: Turn) -> None:
        turn.cancel_event = asyncio.Event()
        turn.disposition = None
        turn.status = "queued"
        turn.started_at = None
        turn.enqueued_at = self._clock()
        self._queue.append(turn)
        self._log(turn.turn_id, "requeued", self.source)
        self._tick.set()

    async def _fail_queue(self, turn: Turn, waited: float, limit: float) -> None:
        turn.status = "failed"
        turn.disposition = "queue_deadline"
        text = QUEUE_FAILURE[turn.kind]
        logger.info(
            "turn %s hop queue_deadline waited %.3fs limit %.3fs",
            turn.turn_id, waited, limit,
        )
        await self._avatar(turn.turn_id, "idle", None)
        await self._output(turn, text, cancelled=False, reason="queue_deadline")
        if turn.kind == "voice":
            await self._say(turn, text)

    async def _generate(self, turn: Turn) -> None:
        deadline = self.limits.generation_s[turn.kind]
        max_tokens = self.limits.max_tokens[turn.kind]
        messages = self._messages(turn)
        started = self._clock()
        parts: list[str] = []
        stream = self.llm.chat_stream(
            self.model,
            messages,
            temperature=0.7,
            max_tokens=max_tokens,
            timeout=deadline,
            stop=STOP_SEQUENCES,
        )
        try:
            parts = await self._consume(turn, stream, started, deadline, max_tokens)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("turn %s generation failed", turn.turn_id)
            turn.disposition = turn.disposition or "error"
        finally:
            await _close_stream(stream)

        if turn.disposition == "requeue":
            return
        if turn.disposition == "cancel":
            turn.status = "cancelled"
            turn.attempts += 1
            text = "".join(parts).strip()
            await self._stream(turn, "", text, done=True, cancelled=True)
            await self._finish_cancelled(turn, text)
            return
        if turn.disposition == "deadline":
            turn.status = "failed"
            turn.attempts += 1
            text = "".join(parts).strip() or GENERATION_FAILURE[turn.kind]
            self._log(turn.turn_id, "generation_deadline", self.source)
            await self._stream(turn, "", text, done=True, cancelled=False)
            await self._avatar(turn.turn_id, "idle", None)
            await self._output(turn, text, cancelled=False, reason="generation_deadline")
            if turn.kind == "voice":
                await self._say(turn, text)
            return
        if turn.disposition == "error":
            turn.status = "failed"
            turn.attempts += 1
            text = ERROR_TEXT[turn.kind]
            await self._avatar(turn.turn_id, "idle", None)
            await self._output(turn, text, cancelled=False, reason="error")
            if turn.kind == "voice":
                await self._say(turn, text)
            return

        text = "".join(parts).strip() or "I need a moment."
        turn.status = "completed"
        turn.attempts += 1
        history = self._history.setdefault(turn.user, [])
        history.append({"role": "user", "content": turn.text})
        history.append({"role": "assistant", "content": text})
        del history[:-20]
        await self._stream(turn, "", text, done=True, cancelled=False)
        emotion = "neutral"
        if turn.kind == "voice":
            await self._avatar(turn.turn_id, "speaking", emotion)
            await self._output(turn, text, cancelled=False, reason=None, emotion=emotion)
            await self._say(turn, text, emotion)
        else:
            await self._avatar(turn.turn_id, "idle", emotion)
            await self._output(turn, text, cancelled=False, reason=None, emotion=emotion)
        self._log(turn.turn_id, "completed", self.source)

    async def _consume(
        self,
        turn: Turn,
        stream: Any,
        started: float,
        deadline: float,
        max_tokens: int,
    ) -> list[str]:
        iterator = stream.__aiter__()
        parts: list[str] = []
        first = True
        while len(parts) < max_tokens:
            if turn.cancel_event.is_set():
                return parts
            remaining = deadline - (self._clock() - started)
            if remaining <= 0:
                turn.disposition = turn.disposition or "deadline"
                return parts
            token, status = await _next_token(iterator, turn.cancel_event, remaining)
            if status == "end":
                return parts
            if status == "cancel":
                return parts
            if status == "timeout":
                turn.disposition = turn.disposition or "deadline"
                return parts
            if status == "error":
                turn.disposition = turn.disposition or "error"
                return parts
            if not token:
                continue
            parts.append(token)
            if first:
                self._log(turn.turn_id, "first_token", self.source)
                first = False
            await self._stream(turn, token, "".join(parts), done=False, cancelled=False)
        return parts

    def _messages(self, turn: Turn) -> list[dict[str, str]]:
        when = local_time_phrase()
        system = (
            "You are NANO on Jack's Jetson Orin Nano, host nanob. "
            "Reply in one or two sentences. "
            f"Local time is {when}. "
            "You cannot run shell commands, sudo, or network scans."
        )
        messages = [{"role": "system", "content": system}]
        messages.extend(self._history.get(turn.user, [])[-20:])
        messages.append({"role": "user", "content": turn.text})
        return messages

    async def _finish_cancelled(self, turn: Turn, text: str = "") -> None:
        await self._avatar(turn.turn_id, "idle", None)
        await self._output(turn, text, cancelled=True, reason=turn.cancel_reason)
        self._log(turn.turn_id, "cancelled", self.source)

    async def _stream(self, turn: Turn, token: str, partial: str, *, done: bool, cancelled: bool) -> None:
        await self.publisher("chat/stream", {
            "turn_id": turn.turn_id,
            "ts": format_ts(),
            "source": self.source,
            "token": token,
            "partial": partial,
            "done": done,
            "cancelled": cancelled,
        })

    async def _output(
        self,
        turn: Turn,
        text: str,
        *,
        cancelled: bool,
        reason: Optional[str],
        emotion: str = "neutral",
    ) -> None:
        payload: dict[str, Any] = {
            "turn_id": turn.turn_id,
            "ts": format_ts(),
            "source": self.source,
            "text": text,
            "emotion": emotion,
            "cancelled": cancelled,
            "user": turn.user,
            "attempt": turn.attempts,
        }
        if reason:
            payload["reason"] = reason
        await self.publisher("chat/output", payload)

    async def _say(self, turn: Turn, text: str, emotion: str = "neutral") -> None:
        if not text:
            return
        await self.publisher("tts/say", {
            "turn_id": turn.turn_id,
            "ts": format_ts(),
            "source": self.source,
            "text": text,
            "emotion": emotion,
        })
        self._log(turn.turn_id, "tts", self.source)

    async def _avatar(self, turn_id: Optional[str], state: str, emotion: Optional[str]) -> None:
        payload: dict[str, Any] = {
            "ts": format_ts(),
            "source": self.source,
            "state": state,
        }
        if turn_id:
            payload["turn_id"] = turn_id
        if emotion:
            payload["emotion"] = emotion
        await self.publisher("avatar/state", payload)

    def _log(self, turn_id: str, hop: str, source: str) -> None:
        logger.info("turn %s hop %s source %s", turn_id, hop, source)


def _turn_id(payload: dict[str, Any]) -> str:
    value = payload.get("turn_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


async def _next_token(iterator: Any, cancel_event: asyncio.Event, timeout: float) -> tuple[str, str]:
    """Return (token, status) where status is token, end, cancel, timeout, or error.

    The pending read is cancelled when the turn is cancelled or the generation
    deadline expires, which closes the adapter stream instead of waiting for
    another model token.
    """
    next_task = asyncio.create_task(iterator.__anext__())
    cancel_task = asyncio.create_task(cancel_event.wait())
    try:
        done, pending = await asyncio.wait(
            {next_task, cancel_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        next_task.cancel()
        cancel_task.cancel()
        raise
    for pending_task in pending:
        pending_task.cancel()
    if next_task not in done:
        await _drain(next_task)
        if cancel_event.is_set():
            return "", "cancel"
        return "", "timeout"
    cancel_task.cancel()
    await _drain(cancel_task)
    try:
        token = next_task.result()
    except StopAsyncIteration:
        return "", "end"
    except asyncio.CancelledError:
        return "", "cancel"
    except Exception:
        logger.exception("adapter stream failed")
        return "", "error"
    return str(token), "token"


async def _drain(task: asyncio.Task) -> None:
    try:
        await task
    except (asyncio.CancelledError, StopAsyncIteration, Exception):
        return


async def _close_stream(stream: Any) -> None:
    close = getattr(stream, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception:
        logger.debug("stream close", exc_info=True)
