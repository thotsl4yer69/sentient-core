"""ALSA capture and playback through ``arecord`` / ``aplay``.

Subprocesses instead of PyAudio: no compiled wheel needed on aarch64, and a
USB device vanishing just ends the subprocess, which we notice and recover from
by re-resolving the card by name (it may come back with a different number).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger("sc.voice.audio")

RATE = 16000
FRAME = 1280  # 80 ms, openWakeWord's native frame size (VAD buffers to 512-sample windows itself)
CARDS = Path("/proc/asound/cards")
_CARD_LINE = re.compile(r"^\s*(\d+)\s+\[([^\]]+?)\s*\]:\s*(.*)$")


@dataclass(frozen=True)
class Card:
    index: int
    id: str
    name: str


def list_cards(text: str | None = None) -> list[Card]:
    if text is None:
        try:
            text = CARDS.read_text()
        except OSError:
            return []
    cards = []
    for line in text.splitlines():
        m = _CARD_LINE.match(line)
        if m:
            cards.append(Card(int(m.group(1)), m.group(2).strip(), m.group(3).strip()))
    return cards


def resolve_device(match: str, override: str = "", cards_text: str | None = None) -> str | None:
    """ALSA device string for the first card whose id or name contains ``match``.
    ``plughw:CARD=<id>`` survives the card number changing after a replug."""
    if override:
        return override
    needle = match.lower()
    for card in list_cards(cards_text):
        if needle in card.id.lower() or needle in card.name.lower():
            return f"plughw:CARD={card.id},DEV=0"
    return None


class Capture:
    """Async source of 80 ms int16 mono 16 kHz frames that survives unplugging."""

    def __init__(self, match: str, override: str = "", *, silence_alarm_s: float = 8.0):
        self.match = match
        self.override = override
        self.device: str | None = None
        self.silence_alarm_s = silence_alarm_s
        self.digital_silence = False
        self.reconnects = 0
        self._proc: asyncio.subprocess.Process | None = None
        self._stopping = False

    async def stop(self) -> None:
        self._stopping = True
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), 3)
            except asyncio.TimeoutError:
                self._proc.kill()

    async def frames(self) -> AsyncIterator[np.ndarray]:
        backoff = 0.5
        while not self._stopping:
            device = resolve_device(self.match, self.override)
            if device is None:
                if self.device is not None:
                    logger.warning("capture device matching %r disappeared", self.match)
                    self.device = None
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 3.0)
                continue
            self._proc = await asyncio.create_subprocess_exec(
                "arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            if self.device != device:
                if self.device is not None or self.reconnects:
                    logger.info("capture on %s (reconnect %d)", device, self.reconnects)
                else:
                    logger.info("capture on %s", device)
            self.device = device
            backoff = 0.5
            zero_since: float | None = None
            assert self._proc.stdout is not None
            try:
                while True:
                    data = await self._proc.stdout.readexactly(FRAME * 2)
                    frame = np.frombuffer(data, dtype=np.int16)
                    if not frame.any():
                        zero_since = zero_since or time.monotonic()
                        if not self.digital_silence and time.monotonic() - zero_since > self.silence_alarm_s:
                            self.digital_silence = True
                            logger.warning("microphone %s is sending exact digital silence", device)
                    else:
                        zero_since = None
                        if self.digital_silence:
                            logger.info("microphone %s audio is back", device)
                        self.digital_silence = False
                    yield frame
            except asyncio.IncompleteReadError:
                err = b""
                if self._proc.stderr is not None:
                    try:
                        err = await asyncio.wait_for(self._proc.stderr.read(400), 0.5)
                    except asyncio.TimeoutError:
                        pass
                if not self._stopping:
                    self.reconnects += 1
                    logger.warning("capture on %s ended (%s); retrying", device,
                                   err.decode(errors="replace").strip() or "device gone")
                    await asyncio.sleep(0.5)
            finally:
                if self._proc.returncode is None:
                    self._proc.kill()
                    await self._proc.wait()


class Player:
    """Plays int16 mono PCM through ``aplay``; ``stop()`` cuts it off at once."""

    def __init__(self, match: str, override: str = ""):
        self.match = match
        self.override = override
        self._proc: asyncio.subprocess.Process | None = None
        self.playing = False

    async def play(self, pcm: bytes, rate: int) -> bool:
        """Returns True if it played to the end, False if stopped or failed."""
        device = resolve_device(self.match, self.override)
        if device is None:
            logger.warning("no playback device matching %r", self.match)
            return False
        self._proc = await asyncio.create_subprocess_exec(
            "aplay", "-q", "-D", device, "-f", "S16_LE", "-r", str(rate), "-c", "1", "-t", "raw",
            stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        self.playing = True
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.write(pcm)
            try:
                await self._proc.stdin.drain()
                self._proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass
            rc = await self._proc.wait()
            if rc not in (0, -15, -9):
                err = (await self._proc.stderr.read(400)).decode(errors="replace") if self._proc.stderr else ""
                logger.warning("aplay on %s exited %s: %s", device, rc, err.strip())
            return rc == 0
        finally:
            # A cancelled turn (barge-in) must not leave aplay talking.
            if self._proc.returncode is None:
                self._proc.kill()
            self.playing = False

    def stop(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            self._proc.kill()
