"""Score hologram acceptance numbers, or collect them from the Pi 5 kiosk later.

Scoring (no hardware):

    python avatar/tools/measure.py score trace.json

Collection on the display node, after Chromium was started with
--remote-debugging-port=9222:

    python avatar/tools/measure.py collect --cdp http://127.0.0.1:9222 --seconds 600

The renderer exposes window.__avatar.measureSample() while it runs. This script
does not invent samples. A trace that omits a series fails that target.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sentient.services.avatar_stream.measure import score_trace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score avatar acceptance measurements")
    sub = parser.add_subparsers(dest="cmd", required=True)
    score = sub.add_parser("score")
    score.add_argument("trace")
    collect = sub.add_parser("collect")
    collect.add_argument("--cdp", default="http://127.0.0.1:9222")
    collect.add_argument("--seconds", type=int, default=600)
    collect.add_argument("--out", default="-")
    args = parser.parse_args(argv)
    if args.cmd == "score":
        trace = json.loads(Path(args.trace).read_text(encoding="utf-8"))
        report = score_trace(trace)
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0 if report["pass"] else 1
    from avatar.tools.measure_cdp import collect_trace

    trace = collect_trace(args.cdp, args.seconds)
    report = {"trace": trace, "score": score_trace(trace)}
    text = json.dumps(report, indent=2) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
    else:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0 if report["score"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
