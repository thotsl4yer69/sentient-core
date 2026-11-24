"""The whole voice loop with real models, in real time.

Real: Silero VAD, openWakeWord (hey_jarvis), whisper.cpp ``whisper-server``,
Piper, and an ``sc-core`` subprocess. Test-only: the "microphone" plays scripted
Piper speech in real time, and the "speaker" records what Cortana says (and can
echo it back into the microphone to test the self-trigger guard).

Needs: SC_TEST_WHISPER_URL, SC_TEST_PIPER_VOICE, SC_TEST_SILERO, SC_TEST_OWW_DIR.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import httpx
import numpy as np
import pytest

from sc.voice.config import VoiceSettings
from sc.voice.core_client import CoreClient
from sc.voice.loop import VoiceLoop
from sc.voice.speech import PiperTTS, WhisperClient
from sc.voice.vad import SileroVAD

REPO = Path(__file__).resolve().parents[2]
FRAME = 1280


def _need(var):
    v = os.environ.get(var)
    if not v or not (v.startswith("http") or Path(v).exists()):
        pytest.skip(f"{var} not set")
    return v


@pytest.fixture(scope="module")
def models():
    return {
        "whisper": _need("SC_TEST_WHISPER_URL"),
        "piper": Path(_need("SC_TEST_PIPER_VOICE")),
        "silero": Path(_need("SC_TEST_SILERO")),
        "oww": Path(_need("SC_TEST_OWW_DIR")),
    }


@pytest.fixture(scope="module")
def tts(models):
    """Cortana's voice (normal Piper settings)."""
    return PiperTTS(models["piper"])


@pytest.fixture(scope="module")
def person(models):
    """The simulated person's voice: deterministic, so the same words give the
    same audio and whisper gives the same transcript every run."""
    return PiperTTS(models["piper"], deterministic=True)


def to16k(pcm: bytes, rate: int) -> np.ndarray:
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    t = np.arange(0, len(x), rate / 16000)
    return np.interp(t, np.arange(len(x)), x).astype(np.int16)


async def speech(tts, text) -> np.ndarray:
    return to16k(await tts.synth(text), tts.rate)


def silence(seconds):
    return np.zeros(int(16000 * seconds), dtype=np.int16)


class Mic:
    """Plays a script of audio in real time, then silence. Anything the speaker
    "plays" while ``echo`` is on is mixed back in (acoustic loopback)."""

    device = "test-mic"

    def __init__(self, script: list[np.ndarray], *, echo: float = 0.0):
        self.queue = np.concatenate(script) if script else np.zeros(0, np.int16)
        self.pos = 0
        self.echo = echo
        self.echo_buf = np.zeros(0, np.int16)
        self.extra: list[np.ndarray] = []

    def add(self, audio: np.ndarray):
        self.queue = np.concatenate([self.queue[self.pos:], audio])
        self.pos = 0

    async def frames(self):
        t0 = time.monotonic()
        n = 0
        while True:
            if self.pos + FRAME <= len(self.queue):
                frame = self.queue[self.pos:self.pos + FRAME].astype(np.int32)
                self.pos += FRAME
            else:
                frame = np.zeros(FRAME, np.int32)
            if self.echo and len(self.echo_buf):
                e = self.echo_buf[:FRAME].astype(np.int32)
                frame[: len(e)] += (e * self.echo).astype(np.int32)
                self.echo_buf = self.echo_buf[FRAME:]
            n += 1
            await asyncio.sleep(max(0.0, t0 + n * 0.08 - time.monotonic()))
            yield np.clip(frame, -32768, 32767).astype(np.int16)


class Speaker:
    def __init__(self, mic: Mic | None = None):
        self.mic = mic
        self.played: list[float] = []
        self.started: list[float] = []
        self.cut_at: list[float] = []
        self.playing = False
        self._stop = asyncio.Event()

    async def play(self, pcm: bytes, rate: int) -> bool:
        self._stop.clear()
        self.playing = True
        self.started.append(time.monotonic())
        dur = len(pcm) / 2 / rate
        if self.mic is not None and self.mic.echo:
            self.mic.echo_buf = np.concatenate([self.mic.echo_buf, to16k(pcm, rate)])
        try:
            await asyncio.wait_for(self._stop.wait(), dur)
            self.played.append(-1.0)
            self.cut_at.append(time.monotonic())
            return False
        except asyncio.TimeoutError:
            self.played.append(dur)
            return True
        except asyncio.CancelledError:
            self.played.append(-1.0)  # cut off by a cancelled turn
            self.cut_at.append(time.monotonic())
            raise
        finally:
            self.playing = False

    def stop(self):
        self._stop.set()


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def core_proc(tmp_path):
    procs = []

    def start(llm_url, temperature=None):
        voice_tok = secrets.token_urlsafe(32)
        (tmp_path / "tokens").write_text(f"jack chat {secrets.token_urlsafe(32)}\nvoice chat+speaker {voice_tok}\n")
        os.chmod(tmp_path / "tokens", 0o600)
        port = _port()
        (tmp_path / "sc.toml").write_text(
            f'bind_port = {port}\nllm_url = "{llm_url}"\ndb_path = "{tmp_path / "sc.db"}"\n'
            f'tokens_file = "{tmp_path / "tokens"}"\n'
            + (f"temperature = {temperature}\n" if temperature is not None else ""))
        env = {**os.environ, "SC_CONFIG": str(tmp_path / "sc.toml"), "PYTHONPATH": str(REPO)}
        with open(tmp_path / "core.log", "w") as log:
            procs.append(subprocess.Popen([sys.executable, "-m", "sc.core", "--voice"], cwd=REPO, env=env,
                                          stdout=log, stderr=subprocess.STDOUT))
        url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                httpx.get(url + "/healthz", timeout=1)
                return url, voice_tok
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError((tmp_path / "core.log").read_text())

    yield start
    for p in procs:
        p.terminate()
        p.wait(10)


def make_loop(models, tts, mic, speaker, core_url, token, *, trigger="wake", stt_url=None, **over):
    opts = {"end_silence_ms": 640, "no_speech_timeout_s": 4.0, "follow_up_s": 2.5, **over}
    s = replace(VoiceSettings(), trigger=trigger, status_path=Path("/tmp/sc-voice-test-status.json"), **opts)
    wake = None
    if trigger == "wake":
        from sc.voice.wake import WakeWord

        wake = WakeWord("hey_jarvis", models["oww"], 0.5)
    return VoiceLoop(
        s, source=mic, sink=speaker, vad=SileroVAD(models["silero"]),
        stt=WhisperClient(stt_url or models["whisper"], 15), tts=tts,
        core=CoreClient(core_url, token, "jack", ()), wake=wake,
    )


async def run_until(loop, cond, timeout):
    task = asyncio.create_task(loop.run())
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < timeout:
            if cond():
                break
            await asyncio.sleep(0.05)
    finally:
        loop.stopped.set()
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await loop.stt.close()
        await loop.core.close()
    return cond()


async def test_wake_question_answer_then_follow_up(models, tts, person, fake_llm, core_proc):
    fake_llm.script.tokens = ["Biscuit", " is", " your", " kelpie.", " Good", " dog."]
    url, tok = await asyncio.to_thread(core_proc, fake_llm.url)
    mic = Mic([silence(0.6), await speech(person, "Hey Jarvis."), silence(0.4),
               await speech(person, "What is my dog called?"), silence(1.0)])
    spk = Speaker()
    loop = make_loop(models, tts, mic, spk, url, tok)
    follow_up = np.concatenate([silence(0.4), await speech(person, "And what breed is she?"), silence(3.0)])

    async def ask_follow_up():
        # Like a person: wait until Cortana has finished and is listening again.
        while not (loop.records and loop.state == "listening"):
            await asyncio.sleep(0.05)
        mic.add(follow_up)

    helper = asyncio.create_task(ask_follow_up())
    ok = await run_until(loop, lambda: len(loop.records) >= 2 and loop.state == "idle", 40)
    helper.cancel()
    assert ok, [(r.transcript, r.outcome) for r in loop.records]
    first, second = loop.records[:2]
    # Exact words vary: Piper adds noise and base.en is small. Check structure.
    assert "called" in first.transcript.lower() and first.outcome == "ok", first.transcript
    assert "she" in second.transcript.lower() and second.outcome == "ok", "follow-up needs no wake word"
    assert first.reply == "Biscuit is your kelpie. Good dog."
    assert sum(d for d in spk.played if d > 0) > 1.5
    print(f"\nend of speech -> first audio: {first.first_audio_s:.2f}s (stt {first.stt_s:.2f}s)")
    sent = fake_llm.requests[0]["messages"]
    assert sent[-1]["content"] == first.transcript
    assert "spoken conversation" in sent[0]["content"], "voice turns use the spoken style rules"


async def test_speech_without_wake_word_is_ignored(models, tts, person, fake_llm, core_proc):
    url, tok = await asyncio.to_thread(core_proc, fake_llm.url)
    mic = Mic([silence(0.5), await speech(person, "What is the weather like today?"), silence(2.0)])
    loop = make_loop(models, tts, mic, Speaker(), url, tok)
    await run_until(loop, lambda: False, 6)
    assert loop.records == [] and fake_llm.requests == []


async def test_own_voice_does_not_retrigger(models, tts, person, fake_llm, core_proc):
    """Cortana's reply is fed straight back into the mic at full volume. It must
    not become a new turn (VAD is off while speaking, guard after playback)."""
    fake_llm.script.tokens = ["What", " is", " my", " dog", " called?", " Hey", " Jarvis."]
    url, tok = await asyncio.to_thread(core_proc, fake_llm.url)
    mic = Mic([silence(0.5), await speech(person, "Hey Jarvis."), silence(0.3),
               await speech(person, "Say something."), silence(8.0)], echo=1.0)
    spk = Speaker(mic)
    loop = make_loop(models, tts, mic, spk, url, tok, follow_up_s=1.0)
    await run_until(loop, lambda: False, 14)
    assert len(loop.records) == 1, [(r.transcript, r.outcome) for r in loop.records]
    assert len(fake_llm.requests) == 1


async def test_barge_in_stops_playback_and_cancels_turn(models, tts, person, fake_llm, core_proc):
    # Generation must still be running at barge-in time (~20 s of tokens) so
    # the test proves the cancel reaches the model, not just the speaker.
    fake_llm.script.tokens = [f" Sentence number {i} is quite long and keeps going." for i in range(40)]
    fake_llm.script.token_delay = 0.5
    url, tok = await asyncio.to_thread(core_proc, fake_llm.url)
    mic = Mic([silence(0.5), await speech(person, "Hey Jarvis."), silence(0.3),
               await speech(person, "Tell me a long story."), silence(0.5)])
    spk = Speaker()
    loop = make_loop(models, tts, mic, spk, url, tok)

    wake_audio = await speech(person, "Hey Jarvis.")
    stop_audio = await speech(person, "Stop.")
    wake_end: list[float] = []

    async def interrupt_when_speaking():
        while loop.state != "speaking":
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.0)
        # A new script object for the next request; the running stream keeps its own.
        fake_llm.script = type(fake_llm.script)(tokens=["Okay", ", stopping."])
        mic.add(np.concatenate([wake_audio, silence(0.3), stop_audio, silence(3.0)]))
        wake_end.append(time.monotonic() + len(wake_audio) / 16000)

    helper = asyncio.create_task(interrupt_when_speaking())
    await run_until(loop, lambda: loop.status.barge_ins >= 1 and len(loop.records) >= 2, 45)
    helper.cancel()
    assert loop.status.barge_ins == 1
    assert loop.records[0].outcome == "barge_in"
    assert -1.0 in spk.played, "playback must be cut off"
    assert "stop" in loop.records[1].transcript.lower()
    await asyncio.sleep(0.5)
    assert fake_llm.disconnects >= 1, "the sc-core turn must be cancelled, not left generating"
    cut_delay = spk.cut_at[0] - wake_end[0]
    print(f"\nbarge-in: playback cut {cut_delay:.2f}s after the wake phrase ended")
    assert cut_delay <= 1.0, "C2: playback must stop within 1 s"
    assert loop.records[1].reply == "Okay, stopping."


async def test_stt_down_speaks_an_apology(models, tts, person, fake_llm, core_proc):
    url, tok = await asyncio.to_thread(core_proc, fake_llm.url)
    mic = Mic([silence(0.3), await speech(person, "Hey Jarvis."), silence(0.3),
               await speech(person, "Are you there?"), silence(3.0)])
    spk = Speaker()
    loop = make_loop(models, tts, mic, spk, url, tok, stt_url="http://127.0.0.1:9")
    await run_until(loop, lambda: bool(loop.records), 15)
    assert loop.records[0].outcome == "stt_error" and spk.played


async def test_core_down_speaks_a_fallback(models, tts, person):
    mic = Mic([silence(0.3), await speech(person, "Hey Jarvis."), silence(0.3),
               await speech(person, "Are you there?"), silence(3.0)])
    spk = Speaker()
    loop = make_loop(models, tts, mic, spk, "http://127.0.0.1:9", "x" * 40)
    await run_until(loop, lambda: bool(loop.records), 15)
    assert loop.records[0].outcome == "core_unavailable" and spk.played


async def test_vad_trigger_mode_answers_every_utterance(models, tts, person, fake_llm, core_proc):
    url, tok = await asyncio.to_thread(core_proc, fake_llm.url)
    mic = Mic([silence(0.5), await speech(person, "Good morning Cortana, how are you?"), silence(3.0)])
    loop = make_loop(models, tts, mic, Speaker(), url, tok, trigger="vad")
    await run_until(loop, lambda: bool(loop.records) and loop.records[0].outcome, 15)
    assert "morning" in loop.records[0].transcript.lower()


async def test_real_model_end_to_end(models, tts, person, real_llm_url, core_proc):
    # temperature 0: the check is that the remembered fact reaches the model and
    # is used, not how a 0.5B test model rolls its dice.
    url, tok = await asyncio.to_thread(core_proc, real_llm_url, temperature=0.0)
    mic = Mic([silence(0.5), await speech(person, "Hey Jarvis."), silence(0.3),
               await speech(person, "Remember that my dog is called Biscuit."), silence(5.0),
               await speech(person, "Hey Jarvis."), silence(0.3),
               await speech(person, "What is my dog called?"), silence(6.0)])
    spk = Speaker()
    loop = make_loop(models, tts, mic, spk, url, tok, follow_up_s=1.0)
    await run_until(loop, lambda: len(loop.records) >= 2 and loop.state == "idle", 60)
    assert len(loop.records) >= 2, [(r.transcript, r.outcome) for r in loop.records]
    last = loop.records[1]
    print(f"\nreal model reply: {last.reply!r}; first audio {last.first_audio_s:.2f}s")
    assert last.outcome == "ok" and last.reply.strip()
    assert "biscuit" in last.reply.lower()
