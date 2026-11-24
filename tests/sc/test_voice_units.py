"""sc-voice pieces: sentence splitting, ALSA card resolution, capture recovery, VAD."""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest

from sc.voice import audio
from sc.voice.text import SentenceSplitter, speakable
from sc.voice.vad import Endpointer

CARDS = """\
 0 [tegrahdaxnx    ]: tegra-hda-xnx - NVIDIA Jetson Orin Nano HDA
                      NVIDIA Jetson Orin Nano HDA at 0x3518000 irq 140
 1 [Device         ]: USB-Audio - USB PnP Sound Device
                      C-Media Electronics Inc. USB PnP Sound Device at usb-3610000.usb-2.3, full speed
 2 [S3             ]: USB-Audio - Anker PowerConf S3
                      Anker PowerConf S3 at usb-3610000.usb-2.4, full speed
"""


def test_splitter_releases_sentences_as_they_complete():
    s = SentenceSplitter()
    out = []
    for piece in ["Hey Jack", ". Biscuit is", " a kelpie! She's", " great at", " herding, really", "."]:
        out += s.feed(piece)
    out += s.flush()
    assert out == ["Hey Jack.", "Biscuit is a kelpie!", "She's great at herding, really."]


def test_splitter_early_first_chunk_and_abbreviations():
    s = SentenceSplitter(first_chunk_words=6)
    first = s.feed("Well, according to what you told me earlier on, the key")
    assert first == ["Well, according to what you told me earlier on,"]
    assert s.feed(" is under the pot, e.g. the blue one. Done") == ["the key is under the pot, e.g. the blue one."]
    assert s.flush() == ["Done"]


def test_speakable_strips_markup():
    assert speakable("**Sure** — here: https://x.y/z 😀 *smiles*") == "Sure — here: the link"
    assert speakable("- item one\n- item two") == "item one item two"


def test_resolve_device_by_name_survives_renumbering():
    assert audio.resolve_device("C-Media", cards_text=CARDS) is None  # only the name line 1 is matched
    assert audio.resolve_device("PnP", cards_text=CARDS) == "plughw:CARD=Device,DEV=0"
    assert audio.resolve_device("powerconf", cards_text=CARDS) == "plughw:CARD=S3,DEV=0"
    renumbered = CARDS.replace(" 2 [S3", " 5 [S3")
    assert audio.resolve_device("PowerConf", cards_text=renumbered) == "plughw:CARD=S3,DEV=0"
    assert audio.resolve_device("Jabra", cards_text=CARDS) is None
    assert audio.resolve_device("x", override="hw:3,0", cards_text=CARDS) == "hw:3,0"
    assert [c.index for c in audio.list_cards(CARDS)] == [0, 1, 2]


@pytest.fixture
def fake_alsa(tmp_path, monkeypatch):
    """Test-only stand-ins for arecord/aplay and /proc/asound/cards.
    arecord emits N frames of tone then exits (as if the USB device vanished)."""
    cards = tmp_path / "cards"
    cards.write_text(CARDS)
    monkeypatch.setattr(audio, "CARDS", cards)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    counter = tmp_path / "runs"
    (bindir / "arecord").write_text(f"""#!/usr/bin/env python3
import sys, math, struct, pathlib
p = pathlib.Path({str(counter)!r}); n = int(p.read_text()) if p.exists() else 0; p.write_text(str(n + 1))
frames = int({str(tmp_path / 'frames')!r} and open({str(tmp_path / 'frames')!r}).read())
amp = 0 if pathlib.Path({str(tmp_path / 'zeros')!r}).exists() else 3000
for i in range(1280 * frames):
    sys.stdout.buffer.write(struct.pack('<h', int(amp * math.sin(i / 8))))
sys.stdout.flush()
sys.stderr.write('arecord: device disconnected\\n')
sys.exit(1)
""")
    (bindir / "aplay").write_text("#!/bin/sh\ncat > /dev/null\n")
    for f in ("arecord", "aplay"):
        os.chmod(bindir / f, os.stat(bindir / f).st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    (tmp_path / "frames").write_text("5")
    return tmp_path


async def test_capture_recovers_after_device_loss(fake_alsa):
    cap = audio.Capture("PowerConf")
    got = 0
    async for frame in cap.frames():
        assert frame.shape == (1280,) and frame.any()
        got += 1
        if got == 12:
            break
    await cap.stop()
    assert cap.reconnects >= 2, "arecord exiting must be retried"
    assert cap.device == "plughw:CARD=S3,DEV=0"
    assert int((fake_alsa / "runs").read_text()) >= 3


async def test_capture_waits_for_device_to_return(fake_alsa):
    cards = Path(audio.CARDS)
    cards.write_text(CARDS.split(" 1 [")[0])  # USB cards unplugged

    async def replug():
        await asyncio.sleep(0.8)
        cards.write_text(CARDS)

    task = asyncio.create_task(replug())
    cap = audio.Capture("PowerConf")
    async for _ in cap.frames():
        break
    await cap.stop()
    await task
    assert cap.device == "plughw:CARD=S3,DEV=0"


async def test_capture_flags_digital_silence(fake_alsa):
    (fake_alsa / "zeros").write_text("1")
    (fake_alsa / "frames").write_text("40")
    cap = audio.Capture("PowerConf", silence_alarm_s=0.0)
    n = 0
    async for _ in cap.frames():
        n += 1
        if n == 3:
            break
    await cap.stop()
    assert cap.digital_silence


async def test_player_plays_and_stops(fake_alsa):
    p = audio.Player("PowerConf")
    assert await p.play(b"\0\0" * 1600, 16000) is True
    assert await audio.Player("Jabra").play(b"\0\0", 16000) is False


async def test_cancelled_playback_kills_aplay(fake_alsa):
    bindir = fake_alsa / "bin"
    (bindir / "aplay").write_text("#!/bin/sh\nexec sleep 30\n")
    p = audio.Player("PowerConf")
    task = asyncio.create_task(p.play(b"\0\0" * 16000, 16000))
    await asyncio.sleep(0.3)
    proc = p._proc
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.1)
    assert proc.returncode is not None, "aplay must be killed when the turn is cancelled"
    assert not p.playing


def test_endpointer_hysteresis():
    ep = Endpointer(threshold=0.5, end_silence_ms=320)
    assert ep.update([0.9, 0.9]) is None
    assert ep.update([0.9]) == "start"
    assert ep.update([0.4] * 5) is None, "0.4 is above the low threshold: still speech"
    assert ep.update([0.1] * 9) is None
    assert ep.update([0.1]) == "end"
    assert ep.heard_speech and not ep.in_speech
