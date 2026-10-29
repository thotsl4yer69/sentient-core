"""Privacy-preserving, cross-service timing markers for one local voice turn.

Use monotonic_ns for durations across processes on the *same* Linux host.
No transcript, audio, prompt, token, credentials or device identifiers logged.
"""
import json
import time


def voice_stage(logger, turn_id, stage, **metrics):
    if not turn_id:
        return
    payload = {
        "v": 1,
        "turn_id": str(turn_id),
        "stage": str(stage),
        "mono_ns": time.monotonic_ns(),
        "epoch_ms": int(time.time() * 1000),
    }
    for key in ("status", "duration_ms"):
        value = metrics.get(key)
        if value is not None:
            payload[key] = value
    logger.info("AIRI_TIMING %s", json.dumps(payload, separators=(",", ":")))
