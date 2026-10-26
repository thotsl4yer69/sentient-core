"""Check cross-service clock math and resilience to corrupt/unrelated journal lines."""
import importlib.util
import logging
from pathlib import Path
from unittest.mock import patch

from sentient.common.voice_timing import voice_stage

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "voice_latency_report", root / "tools/voice_latency_report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def test_voice_stage_contains_no_raw_speech(caplog):
    with patch("sentient.common.voice_timing.time.monotonic_ns", return_value=100):
        with caplog.at_level(logging.INFO):
            voice_stage(logging.getLogger("test_voice"), "turn-1", "wake_received",
                        text="secret transcript", duration_ms=7)
    assert "AIRI_TIMING " in caplog.text
    assert "secret transcript" not in caplog.text
    assert '"duration_ms":7' in caplog.text


def test_report_uses_monotonic_stages_and_survives_noise():
    lines = [
        "system daemon started",
        "AIRI_TIMING not_json",
        'AIRI_TIMING {"v":1,"turn_id":"a","stage":"wake_received","mono_ns":1000000000}',
        'AIRI_TIMING {"v":1,"turn_id":"a","stage":"conversation_received","mono_ns":1400000000}',
        'AIRI_TIMING {"v":1,"turn_id":"a","stage":"context_ready","mono_ns":1700000000}',
        'AIRI_TIMING {"v":1,"turn_id":"a","stage":"tts_synthesis_started","mono_ns":2400000000}',
        'AIRI_TIMING {"v":1,"turn_id":"a","stage":"tts_synthesis_complete","mono_ns":3200000000}',
        'AIRI_TIMING {"v":1,"turn_id":"a","stage":"playback_requested","mono_ns":3300000000}',
    ]
    data = report.build_report(report.read_turns(lines))
    assert data["turn_count"] == 1
    assert data["turns"]["a"]["context_wait_ms"] == 300.0
    assert data["turns"]["a"]["tts_synthesis_ms"] == 800.0
    assert data["turns"]["a"]["largest_measured_stage"] == "tts_synthesis_ms"
    assert data["turns"]["a"]["wake_to_playback_request_ms"] == 2300.0
    assert data["summary"]["playback_requested"]["p95_ms"] == 2300.0
