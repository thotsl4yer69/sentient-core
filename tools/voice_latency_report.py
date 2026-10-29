#!/usr/bin/env python3
"""Summarise AIRI_TIMING records from combined Nano journalctl output.

Example:
  journalctl --since '30 minutes ago' -o cat \
    -u sentient-voice -u sentient-whisper-stt \
    -u sentient-conversation -u sentient-piper-tts \
    | python3 tools/voice_latency_report.py

Metrics are stage markers, not microphone loopback. In particular
'playback_requested' is NOT proof of the first audible sample.
"""
import argparse
import json
import sys

MARKER = "AIRI_TIMING "
MAIN = ("wake_received", "speech_started", "vad_end", "stt_received",
        "stt_result", "conversation_received", "context_ready",
        "llm_first_token", "llm_complete", "tts_received",
        "tts_synthesis_complete", "playback_requested", "tts_complete",
        "turn_finished")
STAGE_PAIRS = {
    "capture_ms": ("wake_received", "vad_end"),
    "stt_queue_ms": ("stt_received", "stt_job_started"),
    "stt_inference_ms": ("stt_inference_started", "stt_inference_complete"),
    "stt_delivery_ms": ("stt_publishing", "stt_result"),
    "conversation_queue_ms": ("conversation_received", "conversation_processing_started"),
    "context_wait_ms": ("conversation_received", "context_ready"),
    "model_first_token_ms": ("llm_started", "llm_first_token"),
    "model_total_ms": ("llm_started", "llm_complete"),
    "tts_queue_ms": ("tts_received", "tts_job_started"),
    "tts_synthesis_ms": ("tts_synthesis_started", "tts_synthesis_complete"),
    "playback_process_ms": ("playback_requested", "tts_complete"),
}



def read_turns(lines):
    turns = {}
    for line in lines:
        pos = line.find(MARKER)
        if pos < 0:
            continue
        try:
            item = json.loads(line[pos + len(MARKER):].strip())
            if item.get("v") != 1:
                continue
            turn_id = item["turn_id"]
            stage = item["stage"]
            mono = int(item["mono_ns"])
            if mono < 0 or not isinstance(stage, str):
                continue
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
        stages = turns.setdefault(turn_id, {})
        # Preserve earliest marker for each stage so duplicates cannot rewrite history.
        stages.setdefault(stage, mono)
    return turns


def quantile(values, frac):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * frac
    low = int(index)
    high = min(len(ordered) - 1, low + 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (index - low), 1)


def build_report(turns):
    per_turn = {}
    samples = {}
    for turn_id, stages in turns.items():
        first = stages.get("wake_received")
        if first is None:
            continue
        deltas = {}
        for stage in MAIN:
            tick = stages.get(stage)
            if tick is not None and tick >= first:
                milliseconds = round((tick - first) / 1_000_000, 1)
                deltas[stage] = milliseconds
                samples.setdefault(stage, []).append(milliseconds)
        if "playback_requested" in deltas:
            deltas["wake_to_playback_request_ms"] = deltas["playback_requested"]
        for metric, (start, stop) in STAGE_PAIRS.items():
            if start in stages and stop in stages and stages[stop] >= stages[start]:
                delta = round((stages[stop] - stages[start]) / 1_000_000, 1)
                deltas[metric] = delta
                samples.setdefault(metric, []).append(delta)
        measured = [(metric, deltas[metric]) for metric in STAGE_PAIRS
                    if metric in deltas and metric not in ("capture_ms", "model_total_ms")]
        if measured:
            worst = max(measured, key=lambda item: item[1])
            deltas["largest_measured_stage"] = worst[0]
        per_turn[turn_id] = deltas
    summary = {
        name: {"count": len(values), "p50_ms": quantile(values, 0.5),
               "p95_ms": quantile(values, 0.95)}
        for name, values in samples.items()
    }
    return {"turn_count": len(per_turn), "summary": summary, "turns": per_turn,
            "note": "Playback requested is not physical first-audio measurement."}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20,
                        help="Maximum per-turn entries in output (default 20)")
    args = parser.parse_args(argv)
    result = build_report(read_turns(sys.stdin))
    result["turns"] = dict(list(result["turns"].items())[-max(0, args.limit):])
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
