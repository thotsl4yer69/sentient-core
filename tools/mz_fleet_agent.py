#!/usr/bin/env python3
"""MZ1312 fleet remote worker with a strict action allowlist."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENDPOINT = os.environ["MZ_FLEET_ENDPOINT"].rstrip("/")
TOKEN = os.environ["MZ_FLEET_TOKEN"]
NODE = os.environ.get("MZ_FLEET_NODE", "orin")
POLL_SECONDS = max(2, int(os.environ.get("MZ_FLEET_POLL_SECONDS", "4")))
TIMEOUT = max(10, int(os.environ.get("MZ_FLEET_ACTION_TIMEOUT", "120")))
STATE_DIR = Path.home() / ".local/state/mz-fleet-agent"
OUTBOX = STATE_DIR / "outbox"
OUTBOX.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "x-mz-agent-token": TOKEN,
    "user-agent": "mz-fleet-agent/2",
    "accept": "application/json",
}

SAFE_SERVICES = {
    "mz1312-project-worker.service",
    "mz1312-project-web.service",
}
SAFE_FLEET_NODES = {"eyepatch", "cortana", "drifter"}
SAFE_TARGET = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def relay(method: str, action: str, payload: dict | None = None):
    query = urllib.parse.urlencode({"action": action, "node": NODE})
    body = None if payload is None else json.dumps(payload).encode()
    headers = dict(HEADERS)
    if body is not None:
        headers["content-type"] = "application/json"
    req = urllib.request.Request(f"{ENDPOINT}?{query}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        if exc.code in (204, 409):
            return exc.code, None
        detail = exc.read().decode("utf-8", "replace")[:2000]
        raise RuntimeError(f"relay HTTP {exc.code}: {detail}") from exc


def run(argv: list[str], timeout: int | None = None) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            argv,
            cwd=str(Path.home()),
            text=True,
            capture_output=True,
            timeout=timeout or TIMEOUT,
            env=os.environ.copy(),
        )
        return p.returncode, p.stdout[-100000:], p.stderr[-100000:]
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return 124, out[-100000:], (err + "\naction timed out")[-100000:]


def find_tailscale() -> str | None:
    return shutil.which("tailscale") or (
        "/usr/bin/tailscale" if Path("/usr/bin/tailscale").exists() else None
    )


def find_mesh() -> str | None:
    for path in (
        Path.home() / "fleet/mesh",
        Path("/mnt/orin-ai/fleet/mesh"),
        Path("/opt/fleet/mesh"),
    ):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def action_host_status(_args: dict) -> tuple[int, str, str]:
    outputs = []
    errors = []
    rc = 0
    for argv in (
        ["hostname"],
        ["id"],
        ["uptime"],
        ["ip", "-brief", "address"],
    ):
        code, out, err = run(argv, 30)
        rc = max(rc, code)
        outputs.append(f"$ {' '.join(argv)}\n{out}".rstrip())
        if err:
            errors.append(f"$ {' '.join(argv)}\n{err}".rstrip())
    return rc, "\n\n".join(outputs), "\n\n".join(errors)


def action_tailscale_status(_args: dict) -> tuple[int, str, str]:
    ts = find_tailscale()
    if not ts:
        return 127, "", "tailscale binary not found"
    outputs, errors = [], []
    rc = 0
    for argv in ([ts, "version"], [ts, "ip"], [ts, "status", "--json"]):
        code, out, err = run(argv, 45)
        rc = max(rc, code)
        outputs.append(f"$ {' '.join(argv)}\n{out}".rstrip())
        if err:
            errors.append(f"$ {' '.join(argv)}\n{err}".rstrip())
    return rc, "\n\n".join(outputs), "\n\n".join(errors)


def action_tailscale_ping(args: dict) -> tuple[int, str, str]:
    target = str(args.get("target", ""))
    if not SAFE_TARGET.fullmatch(target):
        return 2, "", "invalid ping target"
    ts = find_tailscale()
    if not ts:
        return 127, "", "tailscale binary not found"
    return run([ts, "ping", target], 30)


def action_coretana_status(_args: dict) -> tuple[int, str, str]:
    outputs, errors = [], []
    rc = 0
    project_engine = Path.home() / ".local/bin/project-engine"
    commands = [
        ["systemctl", "--user", "is-active", "mz1312-project-worker.service"],
        ["systemctl", "--user", "is-active", "mz1312-project-web.service"],
    ]
    if project_engine.exists():
        commands.append([str(project_engine), "status"])
    for argv in commands:
        code, out, err = run(argv, 45)
        rc = max(rc, code)
        outputs.append(f"$ {' '.join(argv)}\n{out}".rstrip())
        if err:
            errors.append(f"$ {' '.join(argv)}\n{err}".rstrip())
    return rc, "\n\n".join(outputs), "\n\n".join(errors)


def action_service_status(args: dict) -> tuple[int, str, str]:
    service = str(args.get("service", ""))
    if service not in SAFE_SERVICES:
        return 2, "", "service not allowlisted"
    return run(["systemctl", "--user", "--no-pager", "--full", "status", service], 45)


def action_service_restart(args: dict) -> tuple[int, str, str]:
    service = str(args.get("service", ""))
    if service not in SAFE_SERVICES:
        return 2, "", "service not allowlisted"
    code, out, err = run(["systemctl", "--user", "restart", service], 45)
    if code != 0:
        return code, out, err
    c2, o2, e2 = run(["systemctl", "--user", "is-active", service], 30)
    return c2, (out + "\n" + o2).strip(), (err + "\n" + e2).strip()


def action_fleet_status(args: dict) -> tuple[int, str, str]:
    mesh = find_mesh()
    if not mesh:
        return 127, "", "fleet mesh CLI not found in known locations"
    node = str(args.get("node", "")).strip()
    if node and node not in SAFE_FLEET_NODES:
        return 2, "", "fleet node not allowlisted"
    argv = [mesh, "status"] + ([node] if node else [])
    return run(argv, 60)


def action_fleet_doctor(args: dict) -> tuple[int, str, str]:
    mesh = find_mesh()
    if not mesh:
        return 127, "", "fleet mesh CLI not found in known locations"
    node = str(args.get("node", "")).strip()
    if node not in SAFE_FLEET_NODES:
        return 2, "", "fleet node not allowlisted"
    return run([mesh, "doctor", node], 120)


ACTIONS = {
    "host_status": action_host_status,
    "tailscale_status": action_tailscale_status,
    "tailscale_ping": action_tailscale_ping,
    "coretana_status": action_coretana_status,
    "service_status": action_service_status,
    "service_restart": action_service_restart,
    "fleet_status": action_fleet_status,
    "fleet_doctor": action_fleet_doctor,
}


def execute(item: dict) -> dict:
    command_id = int(item["id"])
    try:
        spec = json.loads(str(item["command"]))
    except Exception as exc:
        return result(command_id, 2, "", f"invalid command envelope: {exc}", "invalid")

    action = str(spec.get("action", ""))
    args = spec.get("args") if isinstance(spec.get("args"), dict) else {}
    fn = ACTIONS.get(action)
    if fn is None:
        return result(command_id, 2, "", f"action not allowlisted: {action}", action)

    started = time.monotonic()
    code, stdout, stderr = fn(args)
    return result(command_id, code, stdout, stderr, action, round(time.monotonic() - started, 3))


def result(command_id: int, code: int, stdout: str, stderr: str, action: str, duration: float = 0.0) -> dict:
    return {
        "command_id": command_id,
        "node": NODE,
        "exit_code": code,
        "stdout": stdout,
        "stderr": stderr,
        "metadata": {
            "hostname": socket.gethostname(),
            "action": action,
            "duration_seconds": duration,
            "finished_at": now(),
            "agent_version": 2,
        },
    }


def post_result(payload: dict) -> None:
    status, data = relay("POST", "result", payload)
    if status != 200:
        raise RuntimeError(f"result upload failed: {status} {data}")


def flush_outbox() -> None:
    for path in sorted(OUTBOX.glob("*.json")):
        try:
            post_result(json.loads(path.read_text()))
            path.unlink()
        except Exception as exc:
            print(f"[agent] outbox retry failed: {exc}", file=sys.stderr)
            return


def save_outbox(payload: dict) -> None:
    path = OUTBOX / f"{payload['command_id']}.json"
    path.write_text(json.dumps(payload))
    os.chmod(path, 0o600)


def main() -> int:
    print(f"[agent] node={NODE} allowlisted_actions={','.join(sorted(ACTIONS))}", flush=True)
    while True:
        try:
            flush_outbox()
            status, data = relay("GET", "next")
            if status == 200 and data and data.get("command"):
                payload = execute(data["command"])
                try:
                    post_result(payload)
                except Exception:
                    save_outbox(payload)
                    raise
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"[agent] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
