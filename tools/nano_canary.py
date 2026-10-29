#!/usr/bin/env python3
"""Single-service canary rollout, with automatic rollback on failed restart.

NEVER call this until live inventory confirms the actual Nano checkout,
Python interpreter, audio services and current unit layout.
Uses versioned systemd drop-ins; does not overwrite source or model files.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

SERVICE_MODULES = {
    "sentient-wake-word": "sentient.services.voice.wake_word",
    "sentient-voice": "sentient.services.voice.pipeline",
    "sentient-conversation": "sentient.services.conversation",
    "sentient-whisper-stt": "sentient.services.voice.stt_worker",
    "sentient-piper-tts": "sentient.services.voice.tts_worker",
}


def cmd(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True,
                          timeout=30)


def dropin_for(service):
    return Path("/etc/systemd/system") / (service + ".service.d") / "90-airi-canary.conf"


def rollback_file(backup, dropin):
    previous = backup / "previous.conf"
    if previous.is_file():
        shutil.copy2(previous, dropin)
    elif dropin.exists():
        dropin.unlink()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", choices=sorted(SERVICE_MODULES), required=True)
    parser.add_argument("--checkout", required=True,
                        help="Existing checkout of tested release commit (not the active source)")
    parser.add_argument("--python", required=True,
                        help="Absolute Python interpreter with all installed runtime dependencies")
    parser.add_argument("--expect-sha", required=True,
                        help="Full 40-char release commit SHA to prevent version drift")
    parser.add_argument("--apply", action="store_true",
                        help="Activate canary; default only validates and prints changes")
    parser.add_argument("--backup-root", default="/var/backups/airi-canary")
    args = parser.parse_args()
    repo = Path(args.checkout).resolve()
    interpreter = Path(args.python).resolve()
    sha = cmd("git", "-C", str(repo), "rev-parse", "HEAD").stdout.strip()
    if len(args.expect_sha) != 40 or sha != args.expect_sha:
        raise SystemExit("Source commit does not match expected immutable SHA")
    if not (repo / "sentient/services/voice").is_dir() or not interpreter.is_file():
        raise SystemExit("Missing tested checkout or Python interpreter")
    if cmd("git", "-C", str(repo), "status", "--porcelain").stdout.strip():
        raise SystemExit("Refusing mutable/dirty checkout; use a clean release")
    module = SERVICE_MODULES[args.service]
    unit_file = dropin_for(args.service)
    override = (
        "[Service]\n"
        "WorkingDirectory=" + str(repo) + "\n"
        "Environment=PYTHONPATH=" + str(repo) + "\n"
        "ExecStart=\n"
        "ExecStart=" + str(interpreter) + " -m " + module + "\n"
    )
    report = {"service": args.service, "commit": sha, "python": str(interpreter),
              "override": override, "mode": "apply" if args.apply else "plan"}
    if not args.apply:
        print(json.dumps(report, indent=2))
        return
    if os.geteuid() != 0:
        raise SystemExit("--apply requires root privileges")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path(args.backup_root) / (args.service + "-" + stamp)
    backup.mkdir(parents=True, exist_ok=False)
    unit_file.parent.mkdir(parents=True, exist_ok=True)
    if unit_file.exists():
        shutil.copy2(unit_file, backup / "previous.conf")
    (backup / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    try:
        unit_file.write_text(override)
        cmd("systemctl", "daemon-reload")
        cmd("systemctl", "restart", args.service + ".service")
        cmd("systemctl", "is-active", "--quiet", args.service + ".service")
    except Exception:
        rollback_file(backup, unit_file)
        cmd("systemctl", "daemon-reload", check=False)
        cmd("systemctl", "restart", args.service + ".service", check=False)
        raise
    report["backup_receipt"] = str(backup / "receipt.json")
    report["status"] = "started; full microphone/LLM/TTS soak not yet verified"
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
