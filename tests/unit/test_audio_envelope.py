"""Validate the non-sensitive playback amplitude envelope driving AIRI."""
import math
import struct
import wave

from sentient.services.voice.audio_envelope import wav_envelope


def test_wav_envelope_returns_bounded_levels_and_duration(tmp_path):
    wav_path = tmp_path / "test.wav"
    rate = 16000
    frames = [int(math.sin(2 * math.pi * 440 * t / rate) * 12000)
              for t in range(rate // 2)]
    with wave.open(str(wav_path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(struct.pack("<" + "h" * len(frames), *frames))
    envelope, duration = wav_envelope(wav_path)
    assert len(envelope) <= 450
    assert len(envelope) >= 10
    assert all(0 <= x <= 1 for x in envelope)
    assert any(x > 0.4 for x in envelope)
    assert duration == 0.5


def test_missing_wav_is_not_fabricated(tmp_path):
    assert wav_envelope(tmp_path / "missing.wav") == ([], 0.0)


def test_envelope_does_not_expose_voice_bytes(tmp_path):
    wav_path = tmp_path / "silent.wav"
    with wave.open(str(wav_path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(bytes(6400))
    levels, duration = wav_envelope(wav_path)
    assert duration == 0.2
    assert all(level == 0 for level in levels)
