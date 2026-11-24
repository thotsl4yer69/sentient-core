"""Score a display-node trace against the hologram acceptance targets.

``lead_ms`` is mouth time minus audio time. Positive means the mouth is late
(audio first) and must stay at or under 45 ms. Negative means audio is late
and must stay at or under 125 ms. That is the ITU-R BT.1359 window from the
avatar decision.
"""

from __future__ import annotations

from typing import Mapping

FPS_FLOOR = 30
AUDIO_FIRST_MAX_MS = 45
AUDIO_LATE_MAX_MS = 125
CANCEL_MAX_MS = 150
TURNS_REQUIRED = 10
RECONNECT_MAX_S = 10.0


def _numbers(value: object) -> list[float]:
    if not isinstance(value, list) or not value:
        return []
    out: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError("trace values must be numbers")
        out.append(float(item))
    return out


def score_trace(trace: Mapping[str, object]) -> dict[str, object]:
    """Return a pass/fail report. Missing series fail that target."""
    fps = _numbers(trace.get("fps"))
    leads = _numbers(trace.get("lead_ms"))
    cancels = _numbers(trace.get("cancel_ms"))
    turns = trace.get("turns")
    stuck = bool(trace.get("stuck"))
    reconnect = trace.get("reconnect_s")

    fps_ok = bool(fps) and min(fps) >= FPS_FLOOR
    lead_ok = bool(leads) and all(-AUDIO_LATE_MAX_MS <= item <= AUDIO_FIRST_MAX_MS for item in leads)
    cancel_ok = bool(cancels) and all(item < CANCEL_MAX_MS for item in cancels)
    turns_ok = isinstance(turns, int) and not isinstance(turns, bool) and turns >= TURNS_REQUIRED and not stuck
    if isinstance(reconnect, bool) or not isinstance(reconnect, (int, float)):
        reconnect_ok = False
        reconnect_value = None
    else:
        reconnect_value = float(reconnect)
        reconnect_ok = 0 <= reconnect_value <= RECONNECT_MAX_S

    checks = {
        "fps": {"pass": fps_ok, "min": min(fps) if fps else None, "floor": FPS_FLOOR},
        "audio_to_mouth": {
            "pass": lead_ok,
            "audio_first_max_ms": AUDIO_FIRST_MAX_MS,
            "audio_late_max_ms": AUDIO_LATE_MAX_MS,
        },
        "barge_in": {"pass": cancel_ok, "max_ms": CANCEL_MAX_MS},
        "repeat_turns": {"pass": turns_ok, "turns": turns, "stuck": stuck},
        "reconnect": {"pass": reconnect_ok, "seconds": reconnect_value, "max_s": RECONNECT_MAX_S},
    }
    return {"pass": all(item["pass"] for item in checks.values()), "checks": checks}
