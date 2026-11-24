"""sc-voice settings: ``/etc/sc/voice.toml`` (``SCV_CONFIG``), then ``SCV_*`` env."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

DEFAULT_PATH = Path("/etc/sc/voice.toml")


@dataclass(frozen=True)
class VoiceSettings:
    # sc-core
    core_url: str = "http://127.0.0.1:8770"
    token_file: Path = Path("/etc/sc/voice.token")
    speaker: str = "jack"                 # who is assumed to be talking (no speaker ID yet)
    participants: tuple[str, ...] = ()    # e.g. ("jack", "partner") for a shared room

    # Audio devices (ALSA). The card is found by a case-insensitive substring of
    # its name in /proc/asound/cards, so a replugged USB device that comes back
    # as a different card number is found again. Explicit devices override.
    card_match: str = "USB"
    capture_device: str = ""
    playback_device: str = ""

    # Trigger: "wake" (wake word) or "vad" (every utterance; push-to-talk rigs, tests)
    trigger: str = "wake"
    wake_model: str = "hey_jarvis"        # openWakeWord name, or a path to a trained .onnx
    wake_threshold: float = 0.5
    oww_models_dir: Path = Path("/opt/sc/models/openwakeword")

    # Voice activity detection
    vad_model: Path = Path("/opt/sc/models/silero_vad.onnx")
    vad_threshold: float = 0.5
    end_silence_ms: int = 700
    max_utterance_s: float = 15.0
    no_speech_timeout_s: float = 5.0
    follow_up_s: float = 6.0              # listen again without the wake word after a reply
    playback_guard_ms: int = 350          # ignore the mic this long after playback ends

    # Speech services
    stt_url: str = "http://127.0.0.1:18081"
    stt_timeout_s: float = 15.0
    # Names and words whisper should expect (household names, pets, devices).
    stt_prompt: str = "Cortana, Jack, Biscuit, nanob, Sentient Core."
    tts_voice: Path = Path("/opt/sc/models/piper/en_GB-alba-medium.onnx")
    tts_length_scale: float = 1.0

    status_path: Path = Path("/run/sc-voice/status.json")


def _coerce(value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        return value if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    if isinstance(default, tuple):
        if isinstance(value, (list, tuple)):
            return tuple(str(v).strip().lower() for v in value if str(v).strip())
        return tuple(p.strip().lower() for p in str(value).split(",") if p.strip())
    if isinstance(default, Path):
        return Path(str(value)).expanduser()
    return str(value)


def load_voice_settings(path: Path | None = None, env: Mapping[str, str] | None = None) -> VoiceSettings:
    env = os.environ if env is None else env
    cfg = Path(env["SCV_CONFIG"]) if env.get("SCV_CONFIG") else (path or DEFAULT_PATH)
    values: dict[str, Any] = {}
    if cfg.is_file():
        with cfg.open("rb") as fh:
            data = tomllib.load(fh)
        unknown = set(data) - {f.name for f in fields(VoiceSettings)}
        if unknown:
            raise ValueError(f"unknown settings in {cfg}: {sorted(unknown)}")
        values.update(data)
    for f in fields(VoiceSettings):
        key = "SCV_" + f.name.upper()
        if key in env:
            values[f.name] = env[key]
    defaults = VoiceSettings()
    s = VoiceSettings(**{k: _coerce(v, getattr(defaults, k)) for k, v in values.items()})
    if s.trigger not in ("wake", "vad"):
        raise ValueError("trigger must be 'wake' or 'vad'")
    if not 0 < s.wake_threshold < 1 or not 0 < s.vad_threshold < 1:
        raise ValueError("thresholds must be between 0 and 1")
    if s.end_silence_ms < 200:
        raise ValueError("end_silence_ms must be at least 200")
    return s
