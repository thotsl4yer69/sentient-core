"""One conversational turn, end to end: who's speaking, which session, what to
remember, what to recall, the prompt, the model stream, and the log."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sc.config import Settings
from sc.llm import LLMCancelled, LLMClient, LLMError, LLMTimeout, LLMUnavailable
from sc.memory import MemoryStore, Session, people
from sc.persona import Capabilities, build_messages, history_from_log, load_persona
from sc.turns import Limits, Turn, TurnEngine, TurnPreempted, TurnRejected

logger = logging.getLogger("sc.brain")

# What a voice turn says when it can't produce a real reply. sc-voice speaks
# whatever text comes back, so a failure is never silence.
VOICE_FALLBACK = {
    "rejected": "Sorry, I missed that. Say it again?",
    "llm_timeout": "Sorry, that took me too long. Try me again.",
    "llm_unavailable": "My language model isn't running right now.",
    "llm_error": "Something went wrong while I was thinking. Try again.",
}


@dataclass
class TurnRequest:
    kind: str                       # "voice" or "chat"
    speaker: str                    # who said the latest message
    participants: tuple[str, ...]   # everyone present (includes speaker)
    text: str                       # the latest user message
    client_history: list[dict[str, str]] | None = None  # prior turns if the client keeps them


@dataclass
class TurnResult:
    turn: Turn
    session: Session
    recalled: int
    reply: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def text(self) -> str:
        return "".join(self.reply)


class Brain:
    def __init__(self, settings: Settings, store: MemoryStore, llm: LLMClient, *, voice_enabled: bool = False):
        self.settings = settings
        self.store = store
        self.llm = llm
        self.persona = load_persona(settings.persona_path)
        self.capabilities = Capabilities(voice=voice_enabled)
        self._db_check: tuple[float, bool] = (0.0, True)
        self.engine = TurnEngine(
            llm,
            {
                "voice": Limits(settings.voice_queue_s, settings.voice_first_token_s,
                                settings.voice_total_s, settings.voice_max_tokens),
                "chat": Limits(settings.chat_queue_s, settings.chat_first_token_s,
                               settings.chat_total_s, settings.chat_max_tokens),
            },
            temperature=settings.temperature,
        )

    async def prepare(self, req: TurnRequest) -> tuple[Turn, Session, list[dict[str, str]], int]:
        speaker = people([req.speaker])[0]
        participants = people(list(req.participants) + [speaker])
        text = req.text.strip()
        if not text:
            raise ValueError("empty message")
        session = await asyncio.to_thread(
            self.store.open_session, participants, idle_s=self.settings.session_idle_s
        )
        memories = await asyncio.to_thread(
            self.store.recall, session, text,
            k=self.settings.memory_top_k, min_score=self.settings.memory_min_score,
        )
        if req.client_history is not None:
            history = [m for m in req.client_history if m.get("role") in ("user", "assistant")]
            history = history[-2 * self.settings.history_turns:]
        else:
            log = await asyncio.to_thread(self.store.history, session, self.settings.history_turns)
            history = history_from_log(log, session)
        user_line = f"{speaker.capitalize()}: {text}" if len(participants) > 1 else text
        messages = build_messages(
            persona=self.persona,
            assistant_name=self.settings.assistant_name,
            capabilities=self.capabilities,
            session=session,
            speaker=speaker,
            kind=req.kind,
            memories=memories,
            history=history,
            user_text=user_line,
            timezone=self.settings.timezone,
        )
        turn = Turn(kind=req.kind, user=speaker)
        # Log and remember after recall, so this message can't recall itself.
        await asyncio.to_thread(self.store.log, session, "user", speaker, text, turn_id=turn.id)
        await asyncio.to_thread(self.store.remember, session, speaker, text)
        return turn, session, messages, len(memories)

    async def respond(self, req: TurnRequest, prepared=None) -> AsyncIterator[tuple[str, TurnResult]]:
        """Yield ``(delta, result)`` pairs; ``result`` is the same object each
        time and is complete after the iterator ends. Voice turns never raise
        for model failures: they yield a spoken fallback instead. ``prepared``
        is the result of :meth:`prepare` when the caller needs the turn first."""
        turn, session, messages, recalled = prepared if prepared is not None else await self.prepare(req)
        result = TurnResult(turn=turn, session=session, recalled=recalled)
        started = time.monotonic()
        try:
            async for delta in self.engine.run(turn, messages):
                result.reply.append(delta)
                yield delta, result
        except (TurnRejected, LLMTimeout, LLMUnavailable, LLMError) as exc:
            code = "rejected" if isinstance(exc, TurnRejected) else getattr(exc, "code", "llm_error")
            result.error = code
            logger.warning("turn %s (%s) failed: %s: %s", turn.id, req.kind, code, exc)
            if req.kind == "voice" and not result.reply:
                fallback = VOICE_FALLBACK.get(code, VOICE_FALLBACK["llm_error"])
                result.reply.append(fallback)
                yield fallback, result
            elif req.kind != "voice":
                raise
        except TurnPreempted:
            result.error = "preempted"
        except LLMCancelled:
            result.error = "cancelled"
        finally:
            reply = result.text.strip()
            if reply and result.error not in {"rejected"}:
                try:
                    await asyncio.to_thread(self.store.log, session, "assistant", self.settings.assistant_name.lower(),
                                            reply, turn_id=turn.id)
                except Exception:  # logging must never break a reply
                    logger.exception("could not log assistant reply for %s", turn.id)
            logger.info(
                "turn %s kind=%s speaker=%s session=%s recalled=%d status=%s first_token=%s total=%.2fs tokens=%d",
                turn.id, req.kind, turn.user, session.id, recalled, turn.status,
                f"{turn.stats.first_token_s:.2f}s" if turn.stats.first_token_s is not None else "-",
                time.monotonic() - started, turn.stats.tokens,
            )

    async def health(self) -> dict:
        llm = await self.llm.health()
        # PRAGMA quick_check reads the whole file; once a minute is plenty.
        checked_at, db_ok = self._db_check
        if time.monotonic() - checked_at > 60.0:
            db_ok = await asyncio.to_thread(self.store.check)
            self._db_check = (time.monotonic(), db_ok)
        active = self.engine.active
        return {
            "status": "ok" if (llm["ok"] and db_ok) else "degraded",
            "llm": llm,
            "db": "ok" if db_ok else "corrupt",
            "active_turn": None if active is None else {"id": active.id, "kind": active.kind},
            "queued": self.engine.queued,
            "memory": await asyncio.to_thread(self.store.stats),
        }
