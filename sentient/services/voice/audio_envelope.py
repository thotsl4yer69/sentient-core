"""Compact playback-driven amplitude envelope for AIRI's holographic avatar.

Only derived RMS levels are sent to the browser (no voice/audio payload).
This tracks the *planned* PCM playback timeline, not physical speaker output.
"""
import math
import wave


def wav_envelope(path, frame_ms=40, max_points=450):
    """Return [0..1] per-frame RMS and duration for uncompressed PCM16 WAV."""
    try:
        with wave.open(str(path), "rb") as wav:
            rate = wav.getframerate()
            samples = wav.getnframes()
            channels = wav.getnchannels()
            sample_bytes = wav.getsampwidth()
            duration = samples / rate if rate else 0
            if (rate <= 0 or channels not in (1, 2) or sample_bytes != 2
                    or wav.getcomptype() != "NONE" or duration > 45):
                return [], 0.0
            stride = max(1, int(rate * max(20, frame_ms) / 1000))
            stride = max(stride, math.ceil(samples / max_points))
            levels = []
            import audioop
            while len(levels) < max_points:
                chunk = wav.readframes(stride)
                if not chunk:
                    break
                amplitude = audioop.rms(chunk, 2) / 32768
                levels.append(round(min(1.0, amplitude * 3.0), 3))
            return levels, round(duration, 3)
    except (OSError, EOFError, wave.Error):
        return [], 0.0
