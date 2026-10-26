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
import re
import statistics
import sys

MARKER = "AIRI_TIMING "
MAIN = ("wake_received", "vad_end", "stt_result", "context_ready",
        "llm_first_token", "llm_complete", "tts_synthesis_complete",
        "playback_requested", "tts_complete", "turn_finished")


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
        if "tts_synthesis_complete" in stages and "tts_synthesis_started" in stages:
            deltas["tts_synthesis_ms"] = round((
                stages["tts_synthesis_complete"] - stages["tts_synthesis_started"]
            ) / 1_000_000, 1)
        if "context_ready" in stages and "conversation_received" in stages:
            deltas["context_wait_ms"] = round((
                stages["context_ready"] - stages["conversation_received"]
            ) / 1_000_000, 1)
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
