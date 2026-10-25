#!/usr/bin/env python3
"""Read-only AIRI/Nano deployment inventory.

Returns machine-readable evidence without reading secrets or modifying
services. It may be run through an approved remote shell during handoff.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone
from urllib.request import urlopen

SERVICES = (
    "sentient-wake-word", "sentient-voice", "sentient-whisper-stt",
    "sentient-piper-tts", "sentient-conversation",
    "sentient-contemplation", "sentient-contemplation-http",
    "sentient-memory", "sentient-memory-http", "sentient-perception",
    "sentient-avatar", "sentient-avatar-bridge", "sentient-web-chat",
    "mosquitto", "redis-server", "ollama",
)


def shell(*args, timeout=3):
    """Read-only command; no shell expansion and bounded subprocess time."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {"ok": p.returncode == 0,
                "value": (p.stdout or p.stderr).strip()[:6000]}
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "value": type(exc).__name__}


def read_safe(path, limit=2048):
    try:
        return Path(path).read_text(errors="replace")[:limit].strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def service_inventory(name):
    base = name + ".service"
    result = {
        "active": shell("systemctl", "is-active", base),
        "enabled": shell("systemctl", "is-enabled", base),
    }
    safe_properties = (
        "FragmentPath", "DropInPaths", "WorkingDirectory", "User", "Group",
        "MainPID", "ExecMainStatus", "NRestarts", "ActiveEnterTimestamp",
    )
    # Request each property by name. Never request Environment / EnvironmentFile
    # contents, secrets, tokens, SSH keys or full service logs.
    props = shell("systemctl", "show", base,
                  "--property=" + ",".join(safe_properties))
    result["properties"] = props["value"] if props["ok"] else None
    return result


def get_ollama():
    for url in ("http://127.0.0.1:11434/api/ps",
                "http://127.0.0.1:11434/api/tags"):
        try:
            with urlopen(url, timeout=2) as res:
                data = json.load(res)
            if not isinstance(data, dict):
                raise ValueError("unexpected Ollama API data")
            # Keep only model metadata; no raw request prompts or user content.
            yield {"endpoint": url.rsplit("/", 1)[-1],
                   "models": [
                       {key: m.get(key) for key in ("name", "model", "size", "details")}
                       for m in data.get("models", [])[:30]
                   ]}
        except (OSError, ValueError):
            yield {"endpoint": url.rsplit("/", 1)[-1], "unavailable": True}


def inventory(paths):
    result = {
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "hostname": shell("hostname")["value"],
        "kernel": shell("uname", "-r")["value"],
        "os_release": read_safe("/etc/os-release"),
        "jetson_l4t": read_safe("/etc/nv_tegra_release"),
        "memory": shell("free", "-m")["value"],
        "storage": shell("df", "-h", "/")["value"],
        "sound_cards": read_safe("/proc/asound/cards"),
        "capture_devices": shell("arecord", "-l")["value"],
        "playback_devices": shell("aplay", "-l")["value"],
        "sound_sources": shell("pactl", "list", "short", "sources")["value"],
        "sound_sinks": shell("pactl", "list", "short", "sinks")["value"],
        "tcp_listeners": shell("ss", "-lnt")["value"],
        "ollama": list(get_ollama()),
        "services": {name: service_inventory(name) for name in SERVICES},
        "checkouts": [],
    }
    for root in paths:
        root = Path(root).expanduser()
        exists = root.is_dir()
        checkout = {"path": str(root), "exists": exists}
        if exists:
            checkout.update({
                "sha": shell("git", "-C", str(root), "rev-parse", "HEAD"),
                "changes": shell("git", "-C", str(root), "status", "--porcelain"),
                "python_envs": [
                    str(x) for x in (root / ".venv/bin/python3",
                                      root / "venv/bin/python3")
                    if x.exists()
                ],
            })
        result["checkouts"].append(checkout)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", action="append", default=[])
    parser.add_argument("--output", help="Optional local JSON report path")
    args = parser.parse_args()
    paths = args.checkout or [
        "/opt/sentient-core", "/home/cortana/sentient-core",
        "/mnt/orin-ai", "~/coretana",
    ]
    document = json.dumps(inventory(paths), indent=2)
    if args.output:
        Path(args.output).write_text(document + "\n")
    else:
        print(document)


if __name__ == "__main__":
    main()
