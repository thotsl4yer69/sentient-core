"""One generation at a time, voice first.

The Orin has one GPU and runs llama-server with ``--parallel 1``. The turn engine
serialises generations and enforces the turn rules:

* a voice turn must start within ``voice_queue_s`` (default 2 s) or it is dropped
  with a spoken apology; a chat turn may wait ``chat_queue_s``;
* a voice turn pre-empts a running chat turn. If the chat turn had not produced
  any text yet it is retried once automatically; if it had, its reply ends early;
* any turn can be cancelled by id (barge-in, client disconnect);
* first-token and total deadlines come from the LLM client.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sc.llm import LLMCancelled, LLMClient, StreamStats

logger = logging.getLogger("sc.turns")

KINDS = ("voice", "chat")
_PRIORITY = {"voice": 0, "chat": 1}


class TurnRejected(Exception):
    """The turn could not start in time (queue deadline)."""


class TurnPreempted(Exception):
    """A voice turn took the model after this chat turn had started replying."""


@dataclass
class Turn:
    kind: str
    user: str
    id: str = field(default_factory=lambda: "t-" + uuid.uuid4().hex[:16])
    created: float = field(default_factory=time.monotonic)
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str | None = None
    status: str = "queued"  # queued, running, completed, cancelled, failed, rejected
    stats: StreamStats = field(default_factory=StreamStats)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"turn kind must be one of {KINDS}")

    def stop(self, reason: str) -> None:
        if self.reason is None:
            self.reason = reason
        self.cancel.set()


@dataclass(frozen=True)
class Limits:
    queue_s: float
    first_token_s: float
    total_s: float
    max_tokens: int


class TurnEngine:
    def __init__(self, llm: LLMClient, limits: dict[str, Limits], *, temperature: float):
        missing = set(KINDS) - set(limits)
        if missing:
            raise ValueError(f"limits missing for {sorted(missing)}")
        self.llm = llm
        self.limits = limits
        self.temperature = temperature
        self._cond = asyncio.Condition()
        self._active: Turn | None = None
        self._waiting: list[tuple[int, int, Turn]] = []
        self._seq = itertools.count()
        self._turns: dict[str, Turn] = {}

    # ── introspection ─────────────────────────────────────────────────────
    @property
    def active(self) -> Turn | None:
        return self._active

    @property
    def queued(self) -> int:
        return len(self._waiting)

    def get(self, turn_id: str) -> Turn | None:
        return self._turns.get(turn_id)

    def cancel(self, turn_id: str, reason: str = "client") -> bool:
        turn = self._turns.get(turn_id)
        if turn is None or turn.status in {"completed", "cancelled", "failed", "rejected"}:
            return False
        turn.stop(reason)
        return True

    # ── scheduling ────────────────────────────────────────────────────────
    async def _acquire(self, turn: Turn) -> None:
        limit = self.limits[turn.kind].queue_s
        deadline = time.monotonic() + limit
        entry = (_PRIORITY[turn.kind], next(self._seq), turn)
        async with self._cond:
            self._waiting.append(entry)
            self._waiting.sort(key=lambda e: (e[0], e[1]))
            active = self._active
            if turn.kind == "voice" and active is not None and active.kind == "chat":
                logger.info("voice turn %s pre-empts chat turn %s", turn.id, active.id)
                active.stop("preempted")
            try:
                while True:
                    if turn.cancel.is_set():
                        raise LLMCancelled("cancelled while queued")
                    if self._active is None and self._waiting and self._waiting[0][2] is turn:
                        self._waiting.pop(0)
                        self._active = turn
                        turn.status = "running"
                        return
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TurnRejected(f"{turn.kind} turn could not start within {limit:.1f}s")
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=min(remaining, 0.25))
                    except asyncio.TimeoutError:
                        pass
            except BaseException:
                if entry in self._waiting:
                    self._waiting.remove(entry)
                    self._cond.notify_all()
                raise

    async def _release(self, turn: Turn) -> None:
        async with self._cond:
            if self._active is turn:
                self._active = None
            self._cond.notify_all()

    # ── running a turn ────────────────────────────────────────────────────
    async def run(self, turn: Turn, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        """Yield reply text. Raises TurnRejected, TurnPreempted, LLMCancelled,
        LLMTimeout or LLMUnavailable. ``turn.status`` is final afterwards."""
        self._turns[turn.id] = turn
        if len(self._turns) > 512:
            for old in list(self._turns)[:256]:
                if self._turns[old].status not in {"queued", "running"}:
                    del self._turns[old]
        lim = self.limits[turn.kind]
        retried = False
        try:
            while True:
                try:
                    await self._acquire(turn)
                except TurnRejected:
                    turn.status = "rejected"
                    raise
                emitted = 0
                try:
                    async for delta in self.llm.stream(
                        messages,
                        max_tokens=lim.max_tokens,
                        temperature=self.temperature,
                        first_token_s=lim.first_token_s,
                        total_s=lim.total_s,
                        cancel=turn.cancel,
                        stats=turn.stats,
                    ):
                        emitted += 1
                        yield delta
                    turn.status = "completed"
                    return
                except LLMCancelled:
                    if turn.reason == "preempted":
                        if emitted == 0 and not retried:
                            # Nothing reached the client yet: wait for the voice
                            # turn and start again, once.
                            retried = True
                            turn.reason = None
                            turn.cancel = asyncio.Event()
                            turn.stats = StreamStats()
                            turn.status = "queued"
                            continue
                        turn.status = "cancelled"
                        raise TurnPreempted("a spoken request took priority") from None
                    turn.status = "cancelled"
                    raise
                finally:
                    await self._release(turn)
        except BaseException:
            if turn.status in {"queued", "running"}:
                turn.status = "failed"
            raise
