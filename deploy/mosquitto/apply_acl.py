#!/usr/bin/env python3
"""Build a Mosquitto dynamic-security database from deploy/mosquitto/acl.

The tool talks only to a temporary broker on 127.0.0.1. mosquitto_ctrl has no
TLS, so it must not be pointed at a LAN address. Passwords are read from the
environment (SC_MQTT_PASS_GATEWAY and the other SC_MQTT_PASS_* names) and are
not printed. The admin client created by dynsec init is disabled before the
temporary broker exits; its password is not stored anywhere except the hash
of a random value.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Mapping

USERS: tuple[str, ...] = (
    "sc-gateway",
    "sc-brain",
    "sc-speech",
    "sc-buddy",
    "sc-memory",
    "sc-compat",
    "sc-health",
)

_PASSWORD = re.compile(r"[A-Za-z0-9]{16,128}")
_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")


class AclError(RuntimeError):
    pass


def password_env(user: str) -> str:
    suffix = user.removeprefix("sc-").upper().replace("-", "_")
    return f"SC_MQTT_PASS_{suffix}"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def assert_dest_outside_repo(dest: Path) -> None:
    root = repo_root()
    resolved = dest.resolve()
    if resolved == root or root in resolved.parents:
        raise AclError("refusing to write broker secrets inside the repository")


def valid_topic(topic: str) -> bool:
    if not topic or topic.startswith("/") or "//" in topic:
        return False
    parts = topic.split("/")
    if parts[0] == "sc":
        if len(parts) < 3 or parts[1] != "v1":
            return False
    elif parts[0] != "sentient":
        return False
    else:
        if len(parts) < 2:
            return False
    for index, part in enumerate(parts):
        if part == "#":
            return index == len(parts) - 1
        if part == "+":
            continue
        if not part or not _SEGMENT.fullmatch(part):
            return False
    return True


def parse_acl(path: Path) -> dict[str, list[tuple[str, str]]]:
    if not path.is_file():
        raise AclError(f"ACL file not found: {path}")
    grants: dict[str, list[tuple[str, str]]] = {}
    current: str | None = None
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pieces = line.split()
        if pieces[0] == "user" and len(pieces) == 2:
            user = pieces[1]
            if user not in USERS:
                raise AclError(f"line {number}: {user} is not an sc client")
            if user in grants:
                raise AclError(f"line {number}: duplicate user {user}")
            grants[user] = []
            current = user
            continue
        if pieces[0] == "topic" and len(pieces) == 3 and current is not None:
            access, topic = pieces[1], pieces[2]
            if access not in {"read", "write", "readwrite"}:
                raise AclError(f"line {number}: access must be read, write, or readwrite")
            if not valid_topic(topic):
                raise AclError(f"line {number}: topic is not an sc/v1 or sentient path")
            grants[current].append((access, topic))
            continue
        raise AclError(f"line {number}: unsupported ACL line")
    missing = [user for user in USERS if user not in grants]
    extra = [user for user in grants if user not in USERS]
    if missing or extra:
        raise AclError("ACL users must be exactly the sc-* client list")
    for user, rules in grants.items():
        if not rules:
            raise AclError(f"{user} has no topic grants")
    return grants


def load_passwords(env: Mapping[str, str]) -> dict[str, str]:
    passwords: dict[str, str] = {}
    for user in USERS:
        name = password_env(user)
        value = env.get(name, "")
        if not _PASSWORD.fullmatch(value):
            raise AclError(f"{name} must be 16 to 128 letters or digits")
        passwords[user] = value
    return passwords


def find_plugin() -> str:
    roots = [Path("/usr/lib"), Path("/usr/lib64")]
    matches: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        matches.extend(root.glob("mosquitto_dynamic_security.so"))
        matches.extend(root.glob("*/mosquitto_dynamic_security.so"))
    if not matches:
        raise AclError("mosquitto_dynamic_security.so is not installed")
    return str(matches[0])


def _acl_rows(access: str, topic: str) -> list[tuple[str, str]]:
    subscribe = "subscribePattern" if ("#" in topic or "+" in topic) else "subscribeLiteral"
    rows: list[tuple[str, str]] = []
    if access in {"read", "readwrite"}:
        rows.append((subscribe, topic))
        rows.append(("publishClientReceive", topic))
    if access in {"write", "readwrite"}:
        rows.append(("publishClientSend", topic))
    return rows


def _ctrl(port: int, admin_password: str, args: list[str]) -> None:
    command = [
        "mosquitto_ctrl",
        "-h",
        "127.0.0.1",
        "-p",
        str(port),
        "-u",
        "admin",
        "-P",
        admin_password,
        "dynsec",
        *args,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired as exc:
        raise AclError("mosquitto_ctrl timed out") from exc
    detail = (result.stderr or result.stdout or "").strip()
    if result.returncode != 0 or "Connection error" in detail or "\nError:" in f"\n{detail}":
        raise AclError(detail[:400] or "dynsec command failed")


def _ctrl_retry(port: int, admin_password: str, args: list[str]) -> None:
    last: Exception | None = None
    for _ in range(30):
        try:
            _ctrl(port, admin_password, args)
            return
        except AclError as exc:
            last = exc
            text = str(exc).lower()
            if any(token in text for token in ("refused", "timed out", "not found", "connection")):
                time.sleep(0.1)
                continue
            raise
    raise AclError(f"broker control channel did not come up: {last}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_port(port: int, proc: subprocess.Popen, log_path: Path) -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AclError("bootstrap broker exited\n" + _tail(log_path))
        try:
            with socket.create_connection(("127.0.0.1", port), 0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AclError("bootstrap broker did not listen\n" + _tail(log_path))


def _tail(path: Path) -> str:
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-30:])


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def _chmod_private(path: Path) -> None:
    if path.is_dir():
        os.chmod(path, 0o700)
        return
    mode = 0o640 if _mosquitto_group() and os.geteuid() == 0 else 0o600
    os.chmod(path, mode)
    if mode == 0o640:
        shutil.chown(path, group="mosquitto")


def _mosquitto_group() -> bool:
    try:
        import grp

        grp.getgrnam("mosquitto")
    except KeyError:
        return False
    except ImportError:
        return False
    return True


def _assert_database(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 32:
        raise AclError("dynamic-security database was not written")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AclError("dynamic-security database is not valid JSON") from exc
    clients = {item.get("username"): item for item in data.get("clients", [])}
    missing = [user for user in USERS if user not in clients]
    if missing:
        raise AclError("dynamic-security database is missing clients")
    admin = clients.get("admin") or {}
    if admin.get("disabled") is not True:
        raise AclError("admin client was not disabled")
    defaults = data.get("defaultACLAccess") or {}
    if any(defaults.get(key) for key in ("publishClientSend", "publishClientReceive", "subscribe", "unsubscribe")):
        raise AclError("default ACL access must be deny")


def bootstrap(dest: Path, acl_path: Path, env: Mapping[str, str] | None = None) -> Path:
    assert_dest_outside_repo(dest)
    source = os.environ if env is None else env
    passwords = load_passwords(source)
    grants = parse_acl(acl_path)
    plugin = find_plugin()
    if shutil.which("mosquitto") is None or shutil.which("mosquitto_ctrl") is None:
        raise AclError("mosquitto and mosquitto_ctrl must be on PATH")
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    json_path = dest / "dynamic-security.json"
    admin_password = secrets.token_hex(24)
    init = subprocess.run(
        ["mosquitto_ctrl", "dynsec", "init", str(json_path), "admin", admin_password],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if init.returncode != 0:
        raise AclError("could not initialise the dynamic-security database")
    _chmod_private(json_path)

    work = Path(tempfile.mkdtemp(prefix="sc-mosquitto-boot-"))
    port = _free_port()
    log_path = work / "broker.log"
    conf_path = work / "mosquitto.conf"
    conf_path.write_text(
        "\n".join(
            [
                f"listener {port} 127.0.0.1",
                "allow_anonymous false",
                # mosquitto_ctrl connects with an empty client id and the broker
                # assigns one. The production listener still rejects empty ids.
                f"plugin {plugin}",
                f"plugin_opt_config_file {json_path}",
                "persistence false",
                f"log_dest file {log_path}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    os.chmod(conf_path, 0o600)
    proc = subprocess.Popen(
        ["mosquitto", "-c", str(conf_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_port(port, proc, log_path)
        for acl_type in ("publishClientSend", "publishClientReceive", "subscribe", "unsubscribe"):
            _ctrl_retry(port, admin_password, ["setDefaultACLAccess", acl_type, "deny"])
        for user in USERS:
            _ctrl(port, admin_password, ["createRole", user])
            _ctrl(port, admin_password, ["createClient", user, "-p", passwords[user]])
            _ctrl(port, admin_password, ["addClientRole", user, user, "1"])
            for access, topic in grants[user]:
                for acl_type, topic_filter in _acl_rows(access, topic):
                    _ctrl(port, admin_password, ["addRoleACL", user, acl_type, topic_filter, "allow"])
        _ctrl(port, admin_password, ["disableClient", "admin"])
    finally:
        _stop(proc)
        shutil.rmtree(work, ignore_errors=True)
    _chmod_private(json_path)
    _assert_database(json_path)
    mode = stat.S_IMODE(json_path.stat().st_mode)
    if mode & 0o077 and not (os.geteuid() == 0 and mode & 0o070 == 0o040 and not mode & 0o007):
        raise AclError("dynamic-security database is too widely readable")

    conf_dir = dest / "conf.d"
    conf_dir.mkdir(exist_ok=True)
    os.chmod(conf_dir, 0o750 if os.geteuid() == 0 else 0o700)
    plugin_conf = conf_dir / "plugin.conf"
    plugin_conf.write_text(
        f"plugin {plugin}\nplugin_opt_config_file {json_path}\n",
        encoding="utf-8",
    )
    os.chmod(plugin_conf, 0o644)
    return json_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply the sc MQTT ACL to a dynamic-security database")
    sub = parser.add_subparsers(dest="command", required=True)
    boot = sub.add_parser("bootstrap")
    boot.add_argument("--dest", type=Path, required=True)
    boot.add_argument("--acl", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "bootstrap":
            bootstrap(args.dest, args.acl)
    except AclError as exc:
        print(f"sc-mosquitto: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
