"""sc-voice entry point: ``python -m sc.voice``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import stat
import sys

from sc.voice.audio import Capture, Player, list_cards, resolve_device
from sc.voice.config import load_voice_settings
from sc.voice.core_client import CoreClient
from sc.voice.loop import VoiceLoop
from sc.voice.speech import PiperTTS, WhisperClient
from sc.voice.vad import SileroVAD

logger = logging.getLogger("sc.voice")


def _read_token(path) -> str:
    st = os.stat(path)
    if st.st_mode & (stat.S_IROTH | stat.S_IWOTH | stat.S_IWGRP):
        raise PermissionError(f"{path} must not be world-readable or group/world-writable")
    token = path.read_text().strip()
    if len(token) < 32 or any(c.isspace() for c in token):
        raise ValueError(f"{path} does not hold a single bearer token")
    return token


async def serve(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sentient Core voice loop (sc-voice)")
    p.add_argument("--log-level", default="info")
    p.add_argument("--list-devices", action="store_true", help="print ALSA cards and the chosen devices, then exit")
    args = p.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    s = load_voice_settings()

    if args.list_devices:
        for c in list_cards():
            print(f"card {c.index}: [{c.id}] {c.name}")
        print("capture  ->", resolve_device(s.card_match, s.capture_device))
        print("playback ->", resolve_device(s.card_match, s.playback_device))
        return 0

    token = _read_token(s.token_file)
    vad = SileroVAD(s.vad_model)
    tts = PiperTTS(s.tts_voice, s.tts_length_scale)
    stt = WhisperClient(s.stt_url, s.stt_timeout_s, s.stt_prompt)
    wake = None
    if s.trigger == "wake":
        from sc.voice.wake import WakeWord

        wake = WakeWord(s.wake_model, s.oww_models_dir, s.wake_threshold)
    core = CoreClient(s.core_url, token, s.speaker, s.participants)
    capture = Capture(s.card_match, s.capture_device)
    player = Player(s.card_match, s.playback_device)
    loop = VoiceLoop(s, source=capture, sink=player, vad=vad, stt=stt, tts=tts, core=core, wake=wake)

    if not await stt.healthy():
        logger.warning("whisper-server at %s is not answering yet; turns will apologise until it does", s.stt_url)
    logger.info("sc-voice ready: trigger=%s wake=%s speaker=%s card~%r",
                s.trigger, getattr(wake, "name", "-"), s.speaker, s.card_match)

    stop = asyncio.Event()
    aio = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        aio.add_signal_handler(sig, stop.set)
    runner = asyncio.create_task(loop.run())
    await stop.wait()
    loop.stopped.set()
    player.stop()
    await capture.stop()
    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass
    await stt.close()
    await core.close()
    logger.info("sc-voice stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(serve(argv))
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        print(f"sc-voice: refusing to start: {exc}", file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
