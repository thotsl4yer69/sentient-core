"""Start the real Mosquitto build from deploy/mosquitto against a temp directory."""

from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MOSQUITTO_DIR = REPO / "deploy" / "mosquitto"


def _load_apply_acl():
    path = MOSQUITTO_DIR / "apply_acl.py"
    spec = importlib.util.spec_from_file_location("sc_apply_acl", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load apply_acl.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["sc_apply_acl"] = module
    spec.loader.exec_module(module)
    return module


apply_acl = _load_apply_acl()


@dataclass
class Broker:
    dest: Path
    plain_port: int
    tls_port: int
    passwords: dict[str, str]
    proc: subprocess.Popen
    ca: Path
    cert: Path

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def listening_addresses(port: int) -> set[str]:
    found: set[str] = set()
    hex_port = f"{port:04X}"
    tcp = Path("/proc/net/tcp")
    if not tcp.is_file():
        return found
    for line in tcp.read_text(encoding="utf-8").splitlines()[1:]:
        fields = line.split()
        local, state = fields[1], fields[3]
        ip_hex, port_hex = local.split(":")
        if port_hex.upper() != hex_port or state != "0A":
            continue
        raw = bytes.fromhex(ip_hex)
        found.add(".".join(str(byte) for byte in raw[::-1]))
    return found


def _wait_port(port: int, proc: subprocess.Popen, log_path: Path) -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        if proc.poll() is not None:
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-1500:] if log_path.is_file() else ""
            raise RuntimeError(f"mosquitto exited\n{detail}")
        try:
            with socket.create_connection(("127.0.0.1", port), 0.2):
                return
        except OSError:
            time.sleep(0.05)
    detail = log_path.read_text(encoding="utf-8", errors="replace")[-1500:] if log_path.is_file() else ""
    raise RuntimeError(f"mosquitto did not listen on {port}\n{detail}")


def render_test_conf(dest: Path, plain_port: int, tls_port: int) -> Path:
    plugin = (dest / "conf.d" / "plugin.conf").read_text(encoding="utf-8").strip()
    data = dest / "data"
    data.mkdir(exist_ok=True)
    log_path = dest / "mosquitto.log"
    certs = dest / "certs"
    lines = [
        "per_listener_settings false",
        "allow_anonymous false",
        "allow_zero_length_clientid false",
        plugin,
        f"listener {plain_port} 127.0.0.1",
        "protocol mqtt",
        f"listener {tls_port} 127.0.0.1",
        "protocol mqtt",
        f"cafile {certs / 'ca.crt'}",
        f"certfile {certs / 'server.crt'}",
        f"keyfile {certs / 'server.key'}",
        "require_certificate false",
        "tls_version tlsv1.2",
        "persistence true",
        f"persistence_location {data}/",
        f"log_dest file {log_path}",
        "connection_messages true",
        "",
    ]
    path = dest / "mosquitto.conf"
    path.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(path, 0o600)
    text = path.read_text(encoding="utf-8")
    if "acl_file" in text or "password_file" in text:
        raise RuntimeError("test broker config must not enable the built-in ACL plugin")
    return path


def prepare_dest(dest: Path, passwords: dict[str, str]) -> None:
    env = os.environ.copy()
    for user, password in passwords.items():
        env[apply_acl.password_env(user)] = password
    script = MOSQUITTO_DIR / "init-broker.sh"
    result = subprocess.run(
        ["bash", str(script), "--dest", str(dest)],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "init-broker.sh failed")[-2000:])


def start_broker(passwords: dict[str, str] | None = None) -> Broker:
    if shutil.which("mosquitto") is None:
        raise RuntimeError("mosquitto is not installed")
    if passwords is None:
        import secrets

        passwords = {user: secrets.token_hex(24) for user in apply_acl.USERS}
    dest = Path(tempfile.mkdtemp(prefix="sc-broker-"))
    try:
        prepare_dest(dest, passwords)
        plain_port = free_port()
        tls_port = free_port()
        while tls_port == plain_port:
            tls_port = free_port()
        conf = render_test_conf(dest, plain_port, tls_port)
        log_path = dest / "mosquitto.log"
        proc = subprocess.Popen(
            ["mosquitto", "-c", str(conf)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait_port(plain_port, proc, log_path)
        _wait_port(tls_port, proc, log_path)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return Broker(
        dest=dest,
        plain_port=plain_port,
        tls_port=tls_port,
        passwords=passwords,
        proc=proc,
        ca=dest / "certs" / "ca.crt",
        cert=dest / "certs" / "server.crt",
    )
