"""Speech-to-text (whisper.cpp ``whisper-server``) and text-to-speech (Piper)."""

from __future__ import annotations

import asyncio
import io
import logging
import re
import wave
from pathlib import Path

import aiohttp
import numpy as np

logger = logging.getLogger("sc.voice.speech")

# whisper hallucinates these on silence/noise; never send them to the brain.
_NOISE = re.compile(
    r"^\s*(\[[^\]]*\]|\([^)]*\)|thank you\.?|thanks for watching\.?|you|bye\.?|\.+|-+)\s*$", re.IGNORECASE
)


def wav_bytes(pcm16: np.ndarray, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm16.astype(np.int16).tobytes())
    return buf.getvalue()


class STTError(Exception):
    pass


class WhisperClient:
    def __init__(self, url: str, timeout_s: float, prompt: str = ""):
        self.url = url.rstrip("/")
        self.prompt = prompt  # vocabulary hint: names whisper should expect
        self.timeout = aiohttp.ClientTimeout(total=timeout_s, connect=3)
        self._http: aiohttp.ClientSession | None = None

    async def _session(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(timeout=self.timeout)
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.closed:
            await self._http.close()

    async def transcribe(self, pcm16: np.ndarray) -> str:
        form = aiohttp.FormData()
        form.add_field("file", wav_bytes(pcm16), filename="utt.wav", content_type="audio/wav")
        form.add_field("response_format", "json")
        form.add_field("temperature", "0.0")
        form.add_field("language", "en")
        if self.prompt:
            form.add_field("prompt", self.prompt)
        try:
            http = await self._session()
            async with http.post(f"{self.url}/inference", data=form) as r:
                if r.status != 200:
                    raise STTError(f"whisper-server HTTP {r.status}: {(await r.text())[:200]}")
                body = await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise STTError(f"whisper-server unreachable: {exc}") from exc
        text = " ".join(str(body.get("text", "")).split())
        return "" if _NOISE.match(text) else text

    async def healthy(self) -> bool:
        try:
            http = await self._session()
            async with http.get(f"{self.url}/", timeout=aiohttp.ClientTimeout(total=3)) as r:
                return r.status < 500
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False


class PiperTTS:
    def __init__(self, voice: Path, length_scale: float = 1.0, *, deterministic: bool = False):
        from piper import PiperVoice, SynthesisConfig

        voice = Path(voice)
        if not voice.is_file():
            raise FileNotFoundError(f"Piper voice missing: {voice}")
        self._voice = PiperVoice.load(str(voice))
        # deterministic=True removes Piper's sampling noise: the same text gives
        # the same audio (tests use it for the simulated person's voice).
        noise = {"noise_scale": 0.0, "noise_w_scale": 0.0} if deterministic else {}
        self._cfg = SynthesisConfig(length_scale=length_scale, **noise)
        self.rate = int(self._voice.config.sample_rate)

    def _synth(self, text: str) -> bytes:
        return b"".join(c.audio_int16_bytes for c in self._voice.synthesize(text, syn_config=self._cfg))

    async def synth(self, text: str) -> bytes:
        return await asyncio.to_thread(self._synth, text)
