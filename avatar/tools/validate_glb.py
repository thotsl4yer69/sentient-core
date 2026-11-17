"""Report whether a GLB has the rig and face the hologram renderer expects."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from avatar.tools.glb import inspect_glb


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m avatar.tools.validate_glb MODEL.glb", file=sys.stderr)
        return 2
    path = Path(args[0])
    report = inspect_glb(path.read_bytes())
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
