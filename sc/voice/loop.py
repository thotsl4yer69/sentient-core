"""The voice loop.

States: ``idle`` → (wake word / speech) → ``listening`` → (end of speech) →
``thinking`` → (first sentence) → ``speaking`` → ``listening`` for a follow-up
window without the wake word → ``idle``.

* Self-trigger guard: while Cortana is speaking the microphone feeds only the
  wake-word detector (for barge-in), never VAD; after playback the mic is
  ignored for ``playback_guard_ms`` and both detectors are reset.
* Barge-in: the wake word during ``thinking``/``speaking`` stops playback,
  cancels the sc-core turn and starts listening.
* Failures always produce speech: STT down, sc-core down, empty transcript
  (silently back to idle — no reply to noise).
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from sc.voice.config import VoiceSettings
from sc.voice.core_client import CoreClient, CoreUnavailable
from sc.voice.speech import STTError
from sc.voice.text import SentenceSplitter
from sc.voice.vad import Endpointer, SileroVAD

logger = logging.getLogger("sc.voice")

FRAME_S = 0.08
PREROLL_FRAMES = 4  # 320 ms kept before speech start (VAD trigger)


async def _reap(task: asyncio.Task | None) -> None:
    """Wait for a cancelled task; a real error inside it is logged, not lost."""
    if task is None:
        return
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("voice task failed")


class Source(Protocol):
    def frames(self) -> AsyncIterator[np.ndarray]: ...


class Sink(Protocol):
    playing: bool

    async def play(self, pcm: bytes, rate: int) -> bool: ...

    def stop(self) -> None: ...


class Detector(Protocol):
    def detected(self, frame: np.ndarray) -> bool: ...

    def reset(self) -> None: ...


@dataclass
class TurnRecord:
    transcript: str = ""
    reply: str = ""
    end_of_speech: float = 0.0
    stt_s: float | None = None
    first_audio_s: float | None = None
    outcome: str = ""


@dataclass
class Status:
    state: str = "idle"
    device: str | None = None
    turns: int = 0
    barge_ins: int = 0
    last: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class VoiceLoop:
    def __init__(
        self,
        settings: VoiceSettings,
        *,
        source: Source,
        sink: Sink,
        vad: SileroVAD,
        stt,
        tts,
        core: CoreClient,
        wake: Detector | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_turn: Callable[[TurnRecord], None] | None = None,
    ):
        if settings.trigger == "wake" and wake is None:
            raise ValueError("trigger 'wake' needs a wake-word detector")
        self.s = settings
        self.source, self.sink, self.vad, self.stt, self.tts, self.core, self.wake = (
            source, sink, vad, stt, tts, core, wake
        )
        self.clock = clock
        self.on_turn = on_turn
        self.endpoint = Endpointer(settings.vad_threshold, settings.end_silence_ms)
        self.status = Status()
        self.state = "idle"
        self._utterance: list[np.ndarray] = []
        self._preroll: collections.deque[np.ndarray] = collections.deque(maxlen=PREROLL_FRAMES)
        self._listen_started = 0.0
        self._listen_deadline = 0.0
        self._guard_until = 0.0
        self._turn_task: asyncio.Task | None = None
        self._background: set[asyncio.Task] = set()
        self.records: list[TurnRecord] = []
        self.stopped = asyncio.Event()

    # ── state helpers ─────────────────────────────────────────────────────
    def _set(self, state: str) -> None:
        if state != self.state:
            logger.debug("voice %s -> %s", self.state, state)
        self.state = state
        self.status.state = state
        self._write_status()

    def _write_status(self) -> None:
        path: Path = self.s.status_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self.status)))
            os.replace(tmp, path)
        except OSError:
            pass  # status file is best-effort (e.g. /run not writable in tests)

    def _start_listening(self, now: float, *, window_s: float, include_preroll: bool) -> None:
        self.vad.reset()
        self.endpoint.reset()
        self._utterance = list(self._preroll) if include_preroll else []
        self._preroll.clear()
        self._listen_started = now
        self._listen_deadline = now + window_s
        self._set("listening")

    def _go_idle(self) -> None:
        self.vad.reset()
        self.endpoint.reset()
        if self.wake:
            self.wake.reset()
        self._utterance = []
        self._set("idle")

    # ── main loop ─────────────────────────────────────────────────────────
    async def run(self) -> None:
        try:
            async for frame in self.source.frames():
                await self._on_frame(frame)
                if self.stopped.is_set():
                    break
        finally:
            if self._turn_task and not self._turn_task.done():
                self._turn_task.cancel()
                await _reap(self._turn_task)
            for task in list(self._background):
                await _reap(task)

    async def _on_frame(self, frame: np.ndarray) -> None:
        now = self.clock()
        dev = getattr(self.source, "device", None)
        if dev != self.status.device:
            self.status.device = dev
            self._write_status()
        if now < self._guard_until:
            return
        if self.state in ("thinking", "speaking"):
            if self.wake and self.wake.detected(frame):
                await self._barge_in(now)
            return
        if self.state == "idle":
            if self.s.trigger == "wake":
                if self.wake.detected(frame):  # type: ignore[union-attr]
                    logger.info("wake word heard")
                    self.wake.reset()  # type: ignore[union-attr]
                    self._start_listening(now, window_s=self.s.no_speech_timeout_s, include_preroll=False)
                return
            self._preroll.append(frame)
            if self.endpoint.update(self.vad.process(frame)) == "start":
                self._start_listening(now, window_s=self.s.no_speech_timeout_s, include_preroll=True)
                self.endpoint.in_speech = True
                self.endpoint.heard_speech = True
            return
        if self.state == "listening":
            self._utterance.append(frame)
            event = self.endpoint.update(self.vad.process(frame))
            elapsed = now - self._listen_started
            if event == "end" or (self.endpoint.heard_speech and elapsed >= self.s.max_utterance_s):
                pcm = np.concatenate(self._utterance)
                self._utterance = []
                self._set("thinking")
                self._turn_task = asyncio.create_task(self._turn(pcm, now))
            elif not self.endpoint.heard_speech and now >= self._listen_deadline:
                self._go_idle()

    async def _barge_in(self, now: float) -> None:
        """Never waits on the audio path: a slow network call here would stop
        frames being read (arecord's pipe holds ~2 s) and the listening window
        could expire before the person's next words are processed."""
        logger.info("barge-in")
        self.status.barge_ins += 1
        self.sink.stop()
        task = self._turn_task
        if task and not task.done():
            task.cancel()
            self._spawn(_reap(task))
        # Closing the stream already cancels the turn in sc-core; the explicit
        # cancel is a backstop and runs in the background.
        self._spawn(self.core.cancel(self.core.turn_id))
        if self.wake:
            self.wake.reset()
        self._start_listening(now, window_s=self.s.no_speech_timeout_s, include_preroll=False)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # ── one turn ──────────────────────────────────────────────────────────
    async def _speak(self, text: str, rec: TurnRecord) -> bool:
        pcm = await self.tts.synth(text)
        if rec.first_audio_s is None:
            rec.first_audio_s = self.clock() - rec.end_of_speech
            self._set("speaking")
        return await self.sink.play(pcm, self.tts.rate)

    async def _turn(self, pcm: np.ndarray, end_of_speech: float) -> None:
        rec = TurnRecord(end_of_speech=end_of_speech)
        try:
            t0 = self.clock()
            try:
                rec.transcript = await self.stt.transcribe(pcm)
            except STTError as exc:
                logger.warning("STT failed: %s", exc)
                self.status.errors = (self.status.errors + [f"stt: {exc}"])[-5:]
                rec.outcome = "stt_error"
                await self._speak("Sorry, my hearing isn't working right now.", rec)
                return
            rec.stt_s = self.clock() - t0
            if not rec.transcript:
                rec.outcome = "no_speech"
                return
            logger.info("heard: %r (stt %.2fs)", rec.transcript, rec.stt_s)
            await self._reply(rec)
        except asyncio.CancelledError:
            rec.outcome = "barge_in"
            raise
        finally:
            logger.info(
                "voice turn outcome=%s stt_s=%s first_audio_s=%s transcript=%r",
                rec.outcome or "unknown",
                f"{rec.stt_s:.2f}" if rec.stt_s is not None else "-",
                f"{rec.first_audio_s:.2f}" if rec.first_audio_s is not None else "-",
                rec.transcript,
            )
            self.records.append(rec)
            self.status.turns += 1
            self.status.last = {k: v for k, v in asdict(rec).items() if k != "end_of_speech"}
            if self.on_turn:
                self.on_turn(rec)
            if rec.outcome != "barge_in":
                self._after_turn(spoke=rec.first_audio_s is not None)

    async def _reply(self, rec: TurnRecord) -> None:
        splitter = SentenceSplitter()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        parts: list[str] = []

        async def produce():
            try:
                async for delta in self.core.stream(rec.transcript):
                    parts.append(delta)
                    for sentence in splitter.feed(delta):
                        await queue.put(sentence)
                for sentence in splitter.flush():
                    await queue.put(sentence)
            except CoreUnavailable as exc:
                logger.warning("%s", exc)
                self.status.errors = (self.status.errors + [f"core: {exc}"])[-5:]
                rec.outcome = "core_unavailable"
                if not parts:
                    await queue.put("I can't reach my brain right now. Give me a moment.")
            finally:
                await queue.put(None)

        producer = asyncio.create_task(produce())
        try:
            while True:
                sentence = await queue.get()
                if sentence is None:
                    break
                await self._speak(sentence, rec)
            rec.reply = "".join(parts)
            rec.outcome = rec.outcome or "ok"
        finally:
            if not producer.done():
                producer.cancel()
                await _reap(producer)

    def _after_turn(self, *, spoke: bool) -> None:
        now = self.clock()
        if spoke:
            self._guard_until = now + self.s.playback_guard_ms / 1000.0
            if self.wake:
                self.wake.reset()
            # Follow-up: listen again without the wake word.
            self._start_listening(self._guard_until, window_s=self.s.follow_up_s, include_preroll=False)
        else:
            self._go_idle()
