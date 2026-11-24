"""Acceptance scoring for a trace collected on the display node."""

from __future__ import annotations

from sentient.services.avatar_stream.measure import score_trace


def _good():
    return {
        "fps": [31, 60, 30],
        "lead_ms": [10, -20, 45, -125],
        "cancel_ms": [12, 149],
        "turns": 10,
        "stuck": False,
        "reconnect_s": 4.2,
    }


def test_trace_inside_the_targets_passes():
    report = score_trace(_good())
    assert report["pass"] is True


def test_each_miss_fails_only_its_check():
    slow = _good()
    slow["fps"] = [29, 60]
    audio = _good()
    audio["lead_ms"] = [46]
    late = _good()
    late["lead_ms"] = [-126]
    barge = _good()
    barge["cancel_ms"] = [150]
    short = _good()
    short["turns"] = 9
    stuck = _good()
    stuck["stuck"] = True
    down = _good()
    down["reconnect_s"] = 10.1
    for trace in (slow, audio, late, barge, short, stuck, down):
        assert score_trace(trace)["pass"] is False
    assert score_trace(slow)["checks"]["audio_to_mouth"]["pass"] is True
    assert score_trace(audio)["checks"]["fps"]["pass"] is True
