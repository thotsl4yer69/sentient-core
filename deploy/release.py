"""Versioned sc- releases, health-checked rollback, and exact-unit control.

Gate E swaps one rebuilt component at a time. This module is the off-device
half of that swap: it installs nothing from the legacy sentient-* or
orin-voice units, and it does not retire them (that is WS2).

WS3 ``deploy/build-wheels.sh`` writes pinned wheels to ``dist/wheelhouse`` and a
hashed lock to ``constraints.lock``. Those paths are consumed here. The target
creates ``/opt/sc/releases/<version>/venv`` with ``python -m venv`` and
``pip install --no-index --find-links ... --require-hashes``. A virtualenv is
never copied.

``/opt/sc/current`` is an atomic relative symlink. After the switch, one
AIRI-path chat turn is run. Failure rolls the symlink back. If the release
changed a schema version (Redis key ``sc:schema_version``, SQLite
``PRAGMA user_version``, or ``config_version``), rollback first restores the
snapshot taken before the migration.

WS1 ``tools/orin_backup.py`` is used when it is installed:

    python3 tools/orin_backup.py snapshot --dest DIR --state-root DIR \\
        --components sqlite,redis,config --redis-key sc:schema_version \\
        --redis-url URL
    python3 tools/orin_backup.py restore --snapshot DIR --state-root DIR \\
        --components sqlite,redis,config --redis-key sc:schema_version \\
        --redis-url URL

Until that script is present, the same DB and config files are snapshotted
locally. Migrations run only after that snapshot exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

REDIS_SCHEMA_KEY = "sc:schema_version"
DEFAULT_PREFIX = Path("/opt/sc")
DEFAULT_STATE_ROOT = Path("/var/lib/sc")
DEFAULT_HEALTH_URL = "http://127.0.0.1:8443/v1/chat/completions"
WS3_WHEELHOUSE_RELATIVE = Path("dist/wheelhouse")
WS3_CONSTRAINTS_RELATIVE = Path("constraints.lock")
WS3_BUILD_SCRIPT = Path("deploy/build-wheels.sh")
POWER_MODES = {"15W": 0, "25W": 1, "MAXN_SUPER": 2}
SOAK_POWER_MODE = 1
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_UNIT_RE = re.compile(r"^sc(-[a-z0-9]+)*\.(service|target)$")
_HASH_RE = re.compile(r"--hash=sha256:[0-9a-f]{64}")
_CONFIG_VERSION_RE = re.compile(r"(?m)^config_version\s*=\s*(\d+)\s*$")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

LEGACY_CONFLICTS = {
    "sc-speech.service": frozenset({
        "orin-voice.service",
        "sentient-voice.service",
        "sentient-wake-word.service",
        "sentient-whisper-stt.service",
        "sentient-piper-tts.service",
    }),
    "sc-memory.service": frozenset({
        "sentient-memory.service",
        "sentient-memory-http.service",
    }),
    "sc-brain.service": frozenset({
        "sentient-conversation.service",
        "sentient-contemplation.service",
        "sentient-contemplation-http.service",
    }),
    "sc-gateway.service": frozenset({
        "sentient-web-chat.service",
        "cortana-web.service",
        "cortana-dashboard.service",
        "open-webui.service",
    }),
    "sc-llm.service": frozenset({"ollama.service"}),
    "sc-buddy.service": frozenset(),
    "sc-compat.service": frozenset(),
    "sc-health.service": frozenset(),
}
TARGET_CONFLICTS = frozenset({"sentient-core.target"}).union(*LEGACY_CONFLICTS.values())


class ReleaseError(Exception):
    """The release was not switched, or a restore could not be completed."""


class DeployRolledBack(ReleaseError):
    """Health failed and the previous release (and schema, if needed) is back."""

    def __init__(self, message: str, restored_to: str | None):
        super().__init__(message)
        self.restored_to = restored_to


@dataclass(frozen=True)
class SchemaVersions:
    redis: int
    sqlite: int
    config: int

    def as_dict(self) -> dict:
        return {"redis": self.redis, "sqlite": self.sqlite, "config": self.config}


@dataclass
class DeploySpec:
    version: str
    prefix: Path
    state_root: Path
    wheelhouse: Path
    constraints: Path
    schema_path: Path | None = None
    python: str = sys.executable
    redis_url: str | None = None
    redis_key: str = REDIS_SCHEMA_KEY
    backup_tool: Path | None = None
    health_url: str | None = None
    health_cmd: list[str] | None = None
    health_token: str | None = None
    health_prompt: str = "Reply with the single word pong."
    health_timeout: float = 30.0
    health_fn: Callable[[], None] | None = None


@dataclass
class DeployResult:
    version: str
    current: Path
    schema: SchemaVersions


@dataclass
class RollbackSpec:
    prefix: Path
    state_root: Path
    redis_url: str | None = None
    redis_key: str = REDIS_SCHEMA_KEY
    backup_tool: Path | None = None


def unit_dir() -> Path:
    return Path(__file__).resolve().parent / "units"


def polkit_file() -> Path:
    return Path(__file__).resolve().parent / "polkit" / "10-sc-restart.rules"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def exact_restart_units() -> set[str]:
    return {path.name for path in unit_dir().iterdir() if path.is_file()}


def require_version(version: str) -> str:
    if not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None or version in {".", ".."}:
        raise ReleaseError(f"invalid release version: {version!r}")
    return version


def require_exact_unit(name: str) -> str:
    if not isinstance(name, str) or _UNIT_RE.fullmatch(name) is None or any(ch in name for ch in "*?[]"):
        raise ReleaseError(f"wildcards are not allowed; refusing unit {name!r}")
    allowed = exact_restart_units()
    if name not in allowed:
        raise ReleaseError(f"refusing {name}; restart is limited to exact sc- units")
    return name


def conflicts_for(unit: str) -> frozenset[str]:
    if unit == "sc.target":
        return TARGET_CONFLICTS
    return LEGACY_CONFLICTS.get(unit, frozenset())


def _invoke(systemctl: Callable[[list[str]], None] | None, argv: list[str]) -> None:
    if systemctl is None:
        subprocess.run(argv, check=True)
        return
    systemctl(argv)


def restart_unit(name: str, *, systemctl: Callable[[list[str]], None] | None = None) -> list[str]:
    """Restart one exact unit. The argv is a list, so the shell cannot glob it."""
    argv = ["systemctl", "restart", require_exact_unit(name)]
    _invoke(systemctl, argv)
    return argv


def enable_unit(
    name: str,
    *,
    active: Iterable[str] | None = None,
    systemctl: Callable[[list[str]], None] | None = None,
) -> list[str]:
    """Enable one exact sc- unit without starting it and without touching legacy units."""
    unit = require_exact_unit(name)
    if active is None:
        active = _active_legacy_units()
    active_set = set(active)
    blocked = sorted(conflicts_for(unit) & active_set)
    if blocked:
        raise ReleaseError(
            "refusing to enable "
            + unit
            + " while legacy units are active: "
            + ", ".join(blocked)
            + "; retire them one at a time with tools/orin_reconcile.py (WS2). "
            + "This command will not stop or disable them."
        )
    argv = ["systemctl", "enable", unit]
    _invoke(systemctl, argv)
    return argv


def _active_legacy_units() -> set[str]:
    active = set()
    for name in sorted(TARGET_CONFLICTS):
        proc = subprocess.run(["systemctl", "is-active", name], check=False, capture_output=True, text=True)
        if proc.stdout.strip() in {"active", "activating", "reloading"}:
            active.add(name)
    return active


def install_units(
    dest: Path,
    *,
    source: Path | None = None,
    polkit_dest: Path | None = None,
    dry_run: bool = False,
    systemctl: Callable[[list[str]], None] | None = None,
    reload: bool = False,
) -> list[str]:
    """Copy sc- unit files. Does not enable them and does not follow symlinks."""
    source = Path(source) if source else unit_dir()
    names = sorted(path.name for path in source.iterdir() if path.is_file())
    for name in names:
        if _UNIT_RE.fullmatch(name) is None:
            raise ReleaseError(f"refusing to install {name}")
    if dry_run:
        return names
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        target = dest / name
        if target.is_symlink():
            raise ReleaseError(f"refusing to follow symlink {target}")
    for name in names:
        _atomic_copy(source / name, dest / name)
    if polkit_dest is not None:
        polkit_dest = Path(polkit_dest)
        polkit_dest.mkdir(parents=True, exist_ok=True)
        rule = polkit_dest / polkit_file().name
        if rule.is_symlink():
            raise ReleaseError(f"refusing to follow symlink {rule}")
        _atomic_copy(polkit_file(), rule)
    if reload:
        _invoke(systemctl, ["systemctl", "daemon-reload"])
    return names


def _atomic_copy(src: Path, dest: Path) -> None:
    tmp = dest.parent / f".{dest.name}.{os.getpid()}.tmp"
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)


def parse_nvpmodel_query(text: str) -> int:
    """Map ``nvpmodel -q`` output to 15W=0, 25W=1, MAXN_SUPER=2."""
    digits = [int(line.strip()) for line in text.splitlines() if line.strip() in {"0", "1", "2"}]
    if digits:
        return digits[-1]
    upper = text.upper()
    if "MAXN_SUPER" in upper:
        return 2
    if "15W" in upper:
        return 0
    if "25W" in upper:
        return 1
    raise ReleaseError(f"unrecognized nvpmodel output: {text.strip()!r}")


def set_power_mode(mode: str, *, confirm: bool, runner: Callable[[list[str]], None] | None = None) -> list[str]:
    key = mode.strip().upper().replace(" ", "")
    if key not in POWER_MODES:
        raise ReleaseError(f"unknown power mode {mode!r}; expected 15W, 25W, or MAXN_SUPER")
    argv = ["nvpmodel", "-m", str(POWER_MODES[key])]
    if not confirm:
        raise ReleaseError(f"refusing to set nvpmodel {POWER_MODES[key]} ({key}) without --confirm")
    if runner is None:
        subprocess.run(argv, check=True)
    else:
        runner(argv)
    return argv


def resolve_backup_tool(value: str | None, search_roots: list[Path]) -> Path | None:
    """``builtin`` skips WS1. ``auto`` uses tools/orin_backup.py when it exists."""
    if value in (None, "", "builtin"):
        return None
    if value == "auto":
        for root in search_roots:
            candidate = Path(root) / "tools" / "orin_backup.py"
            if candidate.is_file():
                return candidate
        return None
    path = Path(value)
    if not path.is_file():
        raise ReleaseError(f"backup tool not found: {path}")
    return path


def assert_constraints_hashed(path: Path) -> None:
    path = Path(path)
    if not path.is_file():
        raise ReleaseError(f"constraints file missing: {path}")
    logical: list[str] = []
    buffer = ""
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if buffer:
                raise ReleaseError("constraints.lock has a dangling continuation")
            continue
        if stripped.endswith("\\"):
            buffer += stripped[:-1].strip() + " "
            continue
        buffer += stripped
        logical.append(buffer.strip())
        buffer = ""
    if buffer.strip():
        raise ReleaseError("constraints.lock has a dangling continuation")
    requirements = []
    for entry in logical:
        if entry.startswith("-e ") or "://" in entry or entry.startswith("-"):
            raise ReleaseError(f"unsupported constraint: {entry}")
        requirements.append(entry)
    if not requirements:
        raise ReleaseError("constraints.lock has no requirements")
    for entry in requirements:
        if _HASH_RE.search(entry) is None:
            raise ReleaseError(f"requirement missing sha256 hash: {entry.split()[0]}")


def assert_wheelhouse(path: Path) -> list[Path]:
    path = Path(path)
    if not path.is_dir():
        raise ReleaseError(
            f"wheelhouse {path} is not a directory; consume {WS3_BUILD_SCRIPT} output at {WS3_WHEELHOUSE_RELATIVE}"
        )
    wheels = sorted(path.glob("*.whl"))
    if not wheels:
        raise ReleaseError(
            f"wheelhouse {path} has no wheels; consume {WS3_BUILD_SCRIPT} output at {WS3_WHEELHOUSE_RELATIVE}"
        )
    return wheels


def pip_install_command(venv: Path, wheelhouse: Path, constraints: Path) -> list[str]:
    python = Path(venv) / "bin" / "python"
    return [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--no-index",
        "--only-binary",
        ":all:",
        "--find-links",
        str(wheelhouse),
        "--require-hashes",
        "-r",
        str(constraints),
    ]


def create_venv(python: str, dest: Path) -> None:
    dest = Path(dest)
    if dest.exists() or dest.is_symlink():
        raise ReleaseError(f"refusing to reuse {dest}; virtualenvs are created, never copied")
    proc = subprocess.run([python, "-m", "venv", str(dest)], check=False, capture_output=True, text=True)
    if proc.returncode != 0 or not (dest / "pyvenv.cfg").is_file():
        detail = (proc.stderr or proc.stdout or "python -m venv failed").strip()
        raise ReleaseError(f"venv creation failed: {detail}")


class RedisSchema:
    """Schema integer in Redis, or in a file when ``url`` is ``file`` or unset."""

    def __init__(self, url: str | None, file_path: Path, key: str = REDIS_SCHEMA_KEY):
        self.url = url
        self.file_path = Path(file_path)
        self.key = key
        self._sock: socket.socket | None = None
        self._buffer = b""
        self._authed = False

    def _using_file(self) -> bool:
        return self.url is None or self.url == "file" or self.url.startswith("file:")

    def _resolved_file(self) -> Path:
        if self.url and self.url.startswith("file:"):
            parsed = urllib.parse.urlparse(self.url)
            if parsed.path:
                return Path(parsed.path)
        return self.file_path

    def get(self) -> int | None:
        if self._using_file():
            path = self._resolved_file()
            if not path.exists():
                return None
            text = path.read_text().strip()
            return None if text == "" else int(text)
        raw = self._command("GET", self.key)
        return None if raw is None else int(raw)

    def set(self, version: int) -> None:
        value = str(int(version))
        if self._using_file():
            path = self._resolved_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
            tmp.write_text(value + "\n")
            os.replace(tmp, path)
            return
        self._command("SET", self.key, value)

    def delete(self) -> None:
        if self._using_file():
            path = self._resolved_file()
            if path.is_symlink() or path.exists():
                path.unlink()
            return
        self._command("DEL", self.key)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def _command(self, *parts: str):
        host, port, password = _parse_redis_url(self.url or "")
        sock = self._connect(host, port)
        if password and not self._authed:
            self._send(sock, ["AUTH", password])
            self._read_response(sock)
            self._authed = True
        self._send(sock, list(parts))
        return self._read_response(sock)

    def _connect(self, host: str, port: int) -> socket.socket:
        if self._sock is not None:
            return self._sock
        last: Exception | None = None
        for _ in range(20):
            try:
                self._sock = socket.create_connection((host, port), timeout=2)
                self._sock.settimeout(2)
                return self._sock
            except OSError as exc:
                last = exc
                time.sleep(0.05)
        raise ReleaseError(f"redis connection failed: {last}")

    def _send(self, sock: socket.socket, parts: list[str]) -> None:
        payload = f"*{len(parts)}\r\n".encode()
        for part in parts:
            data = part.encode()
            payload += f"${len(data)}\r\n".encode() + data + b"\r\n"
        sock.sendall(payload)

    def _read_response(self, sock: socket.socket):
        while True:
            value, self._buffer, complete = _parse_one_resp(self._buffer)
            if complete:
                return value
            chunk = sock.recv(4096)
            if not chunk:
                raise ReleaseError("redis connection closed")
            self._buffer += chunk


def _parse_redis_url(url: str) -> tuple[str, int, str | None]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "redis":
        raise ReleaseError(f"unsupported redis url: {url}")
    return parsed.hostname or "127.0.0.1", parsed.port or 6379, parsed.password


def _parse_one_resp(buffer: bytes):
    if not buffer:
        return None, buffer, False
    kind = buffer[:1]
    if kind in {b"+", b"-", b":"}:
        try:
            line, rest = buffer.split(b"\r\n", 1)
        except ValueError:
            return None, buffer, False
        text = line[1:].decode()
        if kind == b"-":
            raise ReleaseError(f"redis error: {text}")
        if kind == b":":
            return int(text), rest, True
        return text, rest, True
    if kind == b"$":
        try:
            header, rest = buffer.split(b"\r\n", 1)
            size = int(header[1:])
        except ValueError:
            return None, buffer, False
        if size < 0:
            return None, rest, True
        if len(rest) < size + 2:
            return None, buffer, False
        return rest[:size].decode(), rest[size + 2 :], True
    raise ReleaseError(f"unexpected redis reply: {buffer[:20]!r}")


def read_config_version(path: Path) -> int:
    if not path.exists():
        return 0
    match = _CONFIG_VERSION_RE.search(path.read_text())
    if match is None:
        raise ReleaseError(f"{path} is missing config_version")
    return int(match.group(1))


def read_sqlite_version(path: Path) -> int:
    if not path.exists():
        return 0
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()


def _redis_client(state_root: Path, redis_url: str | None, redis_key: str) -> RedisSchema:
    url = None if redis_url in (None, "file") else redis_url
    return RedisSchema(url, Path(state_root) / "redis" / "schema_version", redis_key)


def read_schema(state_root: Path, redis_url: str | None, redis_key: str = REDIS_SCHEMA_KEY) -> SchemaVersions:
    state_root = Path(state_root)
    client = _redis_client(state_root, redis_url, redis_key)
    try:
        redis_version = client.get()
    finally:
        client.close()
    return SchemaVersions(
        redis=0 if redis_version is None else redis_version,
        sqlite=read_sqlite_version(state_root / "sc.sqlite"),
        config=read_config_version(state_root / "config" / "sc.toml"),
    )


def load_schema_document(path: Path | None) -> dict:
    if path is None:
        return {}
    path = Path(path)
    if not path.is_file():
        raise ReleaseError(f"schema file missing: {path}")
    document = json.loads(path.read_text())
    if not isinstance(document, dict):
        raise ReleaseError("schema file must be a JSON object")
    return document


def resolve_target(live: SchemaVersions, document: dict) -> SchemaVersions:
    def pick(field: str, current: int) -> int:
        if field not in document:
            return current
        value = document[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ReleaseError(f"{field} must be a non-negative integer")
        return value

    return SchemaVersions(
        pick("redis_version", live.redis),
        pick("sqlite_user_version", live.sqlite),
        pick("config_version", live.config),
    )


def schema_regresses(live: SchemaVersions, target: SchemaVersions) -> bool:
    return target.redis < live.redis or target.sqlite < live.sqlite or target.config < live.config


def _render_scalar(key: str, value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ReleaseError(f"unsupported config value for {key}")
    if isinstance(value, int):
        return f"{key} = {value}"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key} = "{escaped}"'


def apply_config(path: Path, overlay: dict) -> None:
    text = path.read_text() if path.exists() else ""
    for key, value in overlay.items():
        if _KEY_RE.fullmatch(key) is None:
            raise ReleaseError(f"invalid config key {key}")
        rendered = _render_scalar(key, value)
        pattern = re.compile(rf"(?m)^{re.escape(key)}\s*=.*$")
        if pattern.search(text):
            text = pattern.sub(rendered, text, count=1)
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            text += rendered + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    tmp.write_text(text)
    os.replace(tmp, path)


def apply_migrations(state_root: Path, document: dict, target: SchemaVersions, redis_url: str | None, redis_key: str) -> None:
    state_root = Path(state_root)
    database = state_root / "sc.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        for statement in list(document.get("sqlite") or []):
            connection.execute(statement)
        connection.commit()
        connection.execute(f"PRAGMA user_version = {int(target.sqlite)}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    overlay = dict(document.get("config") or {})
    overlay["config_version"] = int(target.config)
    apply_config(state_root / "config" / "sc.toml", overlay)
    client = _redis_client(state_root, redis_url, redis_key)
    try:
        client.set(target.redis)
    finally:
        client.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backup_sqlite(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(src)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def snapshot_state(
    state_root: Path,
    dest: Path,
    redis_url: str | None = None,
    redis_key: str = REDIS_SCHEMA_KEY,
) -> Path:
    """Copy SQLite, config, and the Redis schema key into ``dest`` with hashes."""
    state_root = Path(state_root)
    dest = Path(dest)
    if dest.exists():
        raise ReleaseError(f"snapshot destination already exists: {dest}")
    dest.mkdir(parents=True)
    pairs = {
        "sqlite": (state_root / "sc.sqlite", Path("sqlite/sc.sqlite")),
        "config": (state_root / "config" / "sc.toml", Path("config/sc.toml")),
        "redis": (state_root / "redis" / "schema_version", Path("redis/schema_version")),
    }
    files: dict = {}
    try:
        for name, (src, relative) in pairs.items():
            if not src.exists():
                files[name] = None
                continue
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if name == "sqlite":
                _backup_sqlite(src, target)
            else:
                shutil.copy2(src, target)
            files[name] = {"path": relative.as_posix(), "sha256": _sha256(target)}
        manifest = {
            "producer": "sc-release",
            "redis_key": redis_key,
            "schema": read_schema(state_root, redis_url, redis_key).as_dict(),
            "files": files,
        }
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest


def _delete_sidecars(database: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(database) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            sidecar.unlink()


def _replace_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.{os.getpid()}.restore"
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)


def restore_snapshot(
    state_root: Path,
    snapshot: Path,
    redis_url: str | None = None,
    redis_key: str = REDIS_SCHEMA_KEY,
) -> None:
    """Restore a hashed DB and config snapshot. Hash mismatches leave live state untouched."""
    state_root = Path(state_root)
    snapshot = Path(snapshot)
    manifest = json.loads((snapshot / "manifest.json").read_text())
    files = manifest["files"]
    for name, info in files.items():
        if info is None:
            continue
        path = snapshot / info["path"]
        if not path.is_file() or _sha256(path) != info["sha256"]:
            raise ReleaseError(f"snapshot {name} hash mismatch; refusing to restore")
    live_paths = {
        "sqlite": state_root / "sc.sqlite",
        "config": state_root / "config" / "sc.toml",
        "redis": state_root / "redis" / "schema_version",
    }
    for name, info in files.items():
        live = live_paths[name]
        if name == "sqlite":
            _delete_sidecars(live)
        if info is None:
            if live.is_symlink() or live.exists():
                live.unlink()
            continue
        _replace_file(snapshot / info["path"], live)
    if redis_url and redis_url.startswith("redis://"):
        info = files.get("redis")
        client = RedisSchema(redis_url, live_paths["redis"], redis_key)
        try:
            if info is None:
                client.delete()
            else:
                client.set(int((snapshot / info["path"]).read_text().strip()))
        finally:
            client.close()


def _run_checked(argv: list[str]) -> None:
    proc = subprocess.run(argv, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        raise ReleaseError(detail)


def _ws1_argv(tool: Path, command: str, location: Path, state_root: Path, redis_key: str, redis_url: str | None) -> list[str]:
    flag = "--dest" if command == "snapshot" else "--snapshot"
    return [
        sys.executable,
        str(tool),
        command,
        flag,
        str(location),
        "--state-root",
        str(state_root),
        "--components",
        "sqlite,redis,config",
        "--redis-key",
        redis_key,
        "--redis-url",
        redis_url or "file",
    ]


def take_snapshot(spec: DeploySpec, version: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = Path(spec.state_root) / "snapshots" / f"{version}-{stamp}"
    if spec.backup_tool:
        dest.mkdir(parents=True)
        try:
            _run_checked(_ws1_argv(Path(spec.backup_tool), "snapshot", dest, Path(spec.state_root), spec.redis_key, spec.redis_url))
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        if not (dest / "manifest.json").is_file():
            shutil.rmtree(dest, ignore_errors=True)
            raise ReleaseError("backup tool did not write manifest.json")
        return dest
    return snapshot_state(spec.state_root, dest, spec.redis_url, spec.redis_key)


def restore_recorded(state_root: Path, snapshot: Path, redis_url: str | None, redis_key: str, tool: str | None) -> None:
    if tool:
        _run_checked(_ws1_argv(Path(tool), "restore", Path(snapshot), Path(state_root), redis_key, redis_url))
    else:
        restore_snapshot(state_root, snapshot, redis_url, redis_key)
    _delete_sidecars(Path(state_root) / "sc.sqlite")


def atomic_switch(link: Path, target: Path) -> None:
    link = Path(link)
    target = Path(target)
    if (link.exists() or link.is_symlink()) and not link.is_symlink():
        raise ReleaseError(f"{link} exists and is not a symlink")
    if not target.is_dir():
        raise ReleaseError(f"release target missing: {target}")
    link.parent.mkdir(parents=True, exist_ok=True)
    relative = os.path.relpath(target.resolve(), link.parent.resolve())
    temporary = link.parent / f".{link.name}.{os.getpid()}.tmp"
    if temporary.is_symlink() or temporary.exists():
        temporary.unlink()
    temporary.symlink_to(relative)
    os.replace(temporary, link)


def current_version(prefix: Path) -> str | None:
    link = Path(prefix) / "current"
    if not link.is_symlink():
        return None
    return Path(os.readlink(link)).name


def _remove_link(link: Path) -> None:
    if link.is_symlink() or link.exists():
        if link.exists() and not link.is_symlink():
            raise ReleaseError(f"{link} exists and is not a symlink")
        link.unlink()


def _deploy_state_path(prefix: Path) -> Path:
    return Path(prefix) / "deploy-state.json"


def load_deploy_state(prefix: Path) -> dict:
    path = _deploy_state_path(prefix)
    if not path.exists():
        return {"current": None, "previous": None, "abandoned": None, "releases": {}}
    return json.loads(path.read_text())


def write_deploy_state(prefix: Path, state: dict) -> None:
    path = _deploy_state_path(prefix)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def stage_release(spec: DeploySpec, release_dir: Path, live: SchemaVersions, target: SchemaVersions, snapshot: Path | None) -> None:
    release_dir.mkdir(parents=True)
    try:
        venv = release_dir / "venv"
        create_venv(spec.python, venv)
        env = os.environ.copy()
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        env["PIP_NO_INDEX"] = "1"
        proc = subprocess.run(
            pip_install_command(venv, Path(spec.wheelhouse), Path(spec.constraints)),
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "pip install failed").strip()
            raise ReleaseError(f"pip install failed: {detail}")
        metadata = {
            "version": spec.version,
            "schema_before": live.as_dict(),
            "schema_after": target.as_dict(),
            "snapshot": str(snapshot) if snapshot else None,
            "wheelhouse": str(spec.wheelhouse),
            "constraints": str(spec.constraints),
        }
        (release_dir / "RELEASE.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    except Exception:
        shutil.rmtree(release_dir, ignore_errors=True)
        raise


def run_health(spec: DeploySpec) -> None:
    if spec.health_fn is not None:
        spec.health_fn()
        return
    if spec.health_cmd:
        proc = subprocess.run(list(spec.health_cmd), check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise ReleaseError(f"health command exited {proc.returncode}: {detail}")
        return
    airi_turn(
        spec.health_url or DEFAULT_HEALTH_URL,
        token=spec.health_token,
        prompt=spec.health_prompt,
        timeout=spec.health_timeout,
    )


def airi_turn(url: str, *, token: str | None, prompt: str, timeout: float) -> str:
    """One OpenAI-style chat turn, the post-deploy AIRI-path check."""
    body = json.dumps({
        "model": "sc-local",
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise ReleaseError(f"AIRI-path turn failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ReleaseError(f"AIRI-path turn failed: {exc.reason}") from exc
    if status != 200:
        raise ReleaseError(f"AIRI-path turn failed: HTTP {status}")
    try:
        content = json.loads(raw.decode())["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ReleaseError("AIRI-path turn returned an unexpected body") from exc
    if not isinstance(content, str) or not content.strip():
        raise ReleaseError("AIRI-path turn returned empty content")
    return content


def deploy(spec: DeploySpec) -> DeployResult:
    version = require_version(spec.version)
    prefix = Path(spec.prefix)
    state_root = Path(spec.state_root)
    spec.prefix = prefix
    spec.state_root = state_root
    spec.version = version
    assert_constraints_hashed(Path(spec.constraints))
    assert_wheelhouse(Path(spec.wheelhouse))
    release_dir = prefix / "releases" / version
    if release_dir.exists() or release_dir.is_symlink():
        raise ReleaseError(f"release {version} already exists")
    document = load_schema_document(Path(spec.schema_path) if spec.schema_path else None)
    live = read_schema(state_root, spec.redis_url, spec.redis_key)
    target = resolve_target(live, document)
    if schema_regresses(live, target):
        raise ReleaseError("refusing to deploy a release whose schema is older than the live schema; use rollback")
    state = load_deploy_state(prefix)
    running = current_version(prefix)
    recorded = state.get("current")
    if recorded and running and recorded != running:
        raise ReleaseError(f"deploy-state current {recorded} does not match symlink {running}")
    previous = running
    changed = target != live
    snapshot: Path | None = None
    mutated = False
    try:
        if changed:
            snapshot = take_snapshot(spec, version)
            mutated = True
            apply_migrations(state_root, document, target, spec.redis_url, spec.redis_key)
            after = read_schema(state_root, spec.redis_url, spec.redis_key)
            if after != target:
                raise ReleaseError(f"migration stopped at {after.as_dict()} instead of {target.as_dict()}")
        stage_release(spec, release_dir, live, target, snapshot)
    except Exception:
        if mutated and snapshot is not None:
            restore_recorded(
                state_root,
                snapshot,
                spec.redis_url,
                spec.redis_key,
                str(spec.backup_tool) if spec.backup_tool else None,
            )
        raise

    state.setdefault("releases", {})
    state["releases"][version] = {
        "replaced": previous,
        "snapshot": str(snapshot) if snapshot else None,
        "schema_before": live.as_dict(),
        "schema_after": target.as_dict(),
        "backup_tool": str(spec.backup_tool) if spec.backup_tool else None,
    }
    switched = False
    try:
        atomic_switch(prefix / "current", release_dir)
        switched = True
        run_health(spec)
    except Exception as exc:
        restore_error = None
        if changed and snapshot is not None:
            try:
                restore_recorded(
                    state_root,
                    snapshot,
                    spec.redis_url,
                    spec.redis_key,
                    str(spec.backup_tool) if spec.backup_tool else None,
                )
            except Exception as restore_exc:
                restore_error = restore_exc
        if restore_error is None and switched:
            if previous:
                atomic_switch(prefix / "current", prefix / "releases" / previous)
            else:
                _remove_link(prefix / "current")
        state["abandoned"] = version
        state["current"] = previous if restore_error is None else version
        state["previous"] = None if not previous else state["releases"].get(previous, {}).get("replaced")
        state["last_rollback"] = {
            "version": version,
            "reason": str(exc),
            "restored_to": previous if restore_error is None else None,
            "restore_error": None if restore_error is None else str(restore_error),
        }
        write_deploy_state(prefix, state)
        if restore_error is not None:
            raise ReleaseError(f"rollback restore failed: {restore_error}; current left at {version}") from exc
        raise DeployRolledBack(str(exc), previous) from exc

    state["current"] = version
    state["previous"] = previous
    state["abandoned"] = None
    write_deploy_state(prefix, state)
    return DeployResult(version=version, current=prefix / "current", schema=target)


def rollback(spec: RollbackSpec) -> str:
    prefix = Path(spec.prefix)
    state_root = Path(spec.state_root)
    state = load_deploy_state(prefix)
    current = state.get("current")
    running = current_version(prefix)
    if current and running and current != running:
        raise ReleaseError(f"deploy-state current {current} does not match symlink {running}")
    if not current:
        raise ReleaseError("no current release to roll back")
    entry = state.get("releases", {}).get(current) or {}
    replaced = entry.get("replaced")
    if not replaced:
        raise ReleaseError("no previous release")
    snapshot = entry.get("snapshot")
    tool = spec.backup_tool or entry.get("backup_tool")
    if snapshot:
        restore_recorded(state_root, Path(snapshot), spec.redis_url, spec.redis_key, tool)
    atomic_switch(prefix / "current", prefix / "releases" / replaced)
    state["abandoned"] = current
    state["current"] = replaced
    state["previous"] = (state.get("releases", {}).get(replaced) or {}).get("replaced")
    state["last_rollback"] = {"version": current, "restored_to": replaced, "snapshot": snapshot}
    write_deploy_state(prefix, state)
    return replaced


def status_document(prefix: Path, state_root: Path, redis_url: str | None = None) -> dict:
    prefix = Path(prefix)
    state = load_deploy_state(prefix)
    return {
        "current": current_version(prefix),
        "previous": state.get("previous"),
        "abandoned": state.get("abandoned"),
        "schema": read_schema(Path(state_root), redis_url).as_dict(),
        "prefix": str(prefix),
    }


def _search_roots() -> list[Path]:
    return [Path.cwd(), repo_root()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sc-ctl", description="Operate sc- releases without touching legacy units.")
    sub = parser.add_subparsers(dest="command", required=True)

    deploy_parser = sub.add_parser(
        "deploy",
        description=(
            "Install a versioned release under /opt/sc from pinned wheels. "
            "WS3 deploy/build-wheels.sh writes wheels to dist/wheelhouse and a hashed constraints.lock. "
            "The venv is created on the target and never copied. "
            "A failed AIRI-path health check rolls the symlink back and restores a schema snapshot when versions changed."
        ),
    )
    deploy_parser.add_argument("--version", required=True)
    deploy_parser.add_argument("--prefix", default=str(DEFAULT_PREFIX))
    deploy_parser.add_argument("--state-root", default=str(DEFAULT_STATE_ROOT))
    deploy_parser.add_argument(
        "--wheelhouse",
        default=str(WS3_WHEELHOUSE_RELATIVE),
        help="directory of pinned wheels written by deploy/build-wheels.sh (default: dist/wheelhouse)",
    )
    deploy_parser.add_argument(
        "--constraints",
        default=str(WS3_CONSTRAINTS_RELATIVE),
        help="hashed constraints.lock produced with the wheels",
    )
    deploy_parser.add_argument("--schema")
    deploy_parser.add_argument("--python", default=sys.executable)
    deploy_parser.add_argument("--redis-url", default="redis://127.0.0.1:6379/0")
    deploy_parser.add_argument("--backup-tool", default="auto", help="auto, builtin, or a path to tools/orin_backup.py")
    deploy_parser.add_argument("--health-url", default=DEFAULT_HEALTH_URL)
    deploy_parser.add_argument("--health-cmd", nargs="+")
    deploy_parser.add_argument("--token", default=os.environ.get("SC_HEALTH_TOKEN"))
    deploy_parser.add_argument("--prompt", default="Reply with the single word pong.")
    deploy_parser.add_argument("--timeout", type=float, default=30.0)

    rollback_parser = sub.add_parser("rollback", description="Restore the previous release and its schema snapshot when one was taken.")
    rollback_parser.add_argument("--prefix", default=str(DEFAULT_PREFIX))
    rollback_parser.add_argument("--state-root", default=str(DEFAULT_STATE_ROOT))
    rollback_parser.add_argument("--redis-url", default="redis://127.0.0.1:6379/0")
    rollback_parser.add_argument("--backup-tool", default="auto")

    status_parser = sub.add_parser("status")
    status_parser.add_argument("--prefix", default=str(DEFAULT_PREFIX))
    status_parser.add_argument("--state-root", default=str(DEFAULT_STATE_ROOT))
    status_parser.add_argument("--redis-url", default="redis://127.0.0.1:6379/0")
    status_parser.add_argument("--json", action="store_true")

    install_parser = sub.add_parser("install-units", description="Copy sc- units and the polkit rule. Does not enable or start them.")
    install_parser.add_argument("--unit-dest", required=True)
    install_parser.add_argument("--polkit-dest")
    install_parser.add_argument("--dry-run", action="store_true")
    install_parser.add_argument("--reload", action="store_true")

    enable_parser = sub.add_parser("enable", description="Enable one exact sc- unit without --now and without stopping legacy units.")
    enable_parser.add_argument("unit")
    restart_parser = sub.add_parser("restart", description="Restart one exact sc- unit. Wildcards are rejected.")
    restart_parser.add_argument("unit")

    health_parser = sub.add_parser("health", description="Run one AIRI-path chat turn against sc-gateway.")
    health_parser.add_argument("--url", default=DEFAULT_HEALTH_URL)
    health_parser.add_argument("--token", default=os.environ.get("SC_HEALTH_TOKEN"))
    health_parser.add_argument("--prompt", default="Reply with the single word pong.")
    health_parser.add_argument("--timeout", type=float, default=30.0)

    power = sub.add_parser(
        "power",
        description="Orin Nano Super flash config: 15W = 0, 25W = 1 (soak), MAXN_SUPER = 2.",
    )
    power_sub = power.add_subparsers(dest="power_command", required=True)
    power_sub.add_parser("show")
    power_set = power_sub.add_parser("set")
    power_set.add_argument("mode")
    power_set.add_argument("--confirm", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except DeployRolledBack as exc:
        print(f"ROLLBACK: {exc}", file=sys.stderr)
        return 3
    except ReleaseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _main(argv: list[str] | None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "deploy":
        result = deploy(DeploySpec(
            version=args.version,
            prefix=Path(args.prefix),
            state_root=Path(args.state_root),
            wheelhouse=Path(args.wheelhouse),
            constraints=Path(args.constraints),
            schema_path=Path(args.schema) if args.schema else None,
            python=args.python,
            redis_url=args.redis_url,
            backup_tool=resolve_backup_tool(args.backup_tool, _search_roots()),
            health_url=None if args.health_cmd else args.health_url,
            health_cmd=args.health_cmd,
            health_token=args.token,
            health_prompt=args.prompt,
            health_timeout=args.timeout,
        ))
        print(f"DEPLOYED {result.version}")
        return 0
    if args.command == "rollback":
        restored = rollback(RollbackSpec(
            prefix=Path(args.prefix),
            state_root=Path(args.state_root),
            redis_url=args.redis_url,
            backup_tool=resolve_backup_tool(args.backup_tool, _search_roots()),
        ))
        print(f"ROLLED BACK TO {restored}")
        return 0
    if args.command == "status":
        document = status_document(Path(args.prefix), Path(args.state_root), args.redis_url)
        if args.json:
            print(json.dumps(document, indent=2, sort_keys=True))
        else:
            schema = document["schema"]
            print(
                f"current={document['current']} previous={document['previous']} "
                f"redis={schema['redis']} sqlite={schema['sqlite']} config={schema['config']}"
            )
        return 0
    if args.command == "install-units":
        names = install_units(
            Path(args.unit_dest),
            polkit_dest=Path(args.polkit_dest) if args.polkit_dest else None,
            dry_run=args.dry_run,
            reload=args.reload,
        )
        for name in names:
            print(name)
        return 0
    if args.command == "enable":
        enable_unit(args.unit)
        print(f"ENABLED {args.unit}")
        return 0
    if args.command == "restart":
        restart_unit(args.unit)
        print(f"RESTARTED {args.unit}")
        return 0
    if args.command == "health":
        text = airi_turn(args.url, token=args.token, prompt=args.prompt, timeout=args.timeout)
        print(text)
        return 0
    if args.command == "power":
        if args.power_command == "show":
            proc = subprocess.run(["nvpmodel", "-q"], check=False, capture_output=True, text=True)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "nvpmodel -q failed").strip()
                raise ReleaseError(detail)
            mode = parse_nvpmodel_query(proc.stdout)
            name = {index: label for label, index in POWER_MODES.items()}[mode]
            suffix = " soak default" if mode == SOAK_POWER_MODE else ""
            print(f"{name} (mode {mode}{suffix})")
            return 0
        set_power_mode(args.mode, confirm=args.confirm)
        print(f"POWER {args.mode}")
        return 0
    raise ReleaseError(f"unknown command {args.command}")


if __name__ == "__main__":
    sys.exit(main())
