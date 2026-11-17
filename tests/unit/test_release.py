"""Host-free tests for sc- release, units, polkit, and rollback.

Nothing here contacts the Jetson, enables a legacy unit, or builds wheels.
Wheel input is the layout deploy/build-wheels.sh (WS3) writes.
"""
import base64
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE_PATH = ROOT / "deploy" / "release.py"
SPEC = importlib.util.spec_from_file_location("sc_release", RELEASE_PATH)
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)

UNITS = ROOT / "deploy" / "units"
POLKIT = ROOT / "deploy" / "polkit" / "10-sc-restart.rules"
EXPECTED_UNITS = {
    "sc-brain.service",
    "sc-buddy.service",
    "sc-compat.service",
    "sc-gateway.service",
    "sc-health.service",
    "sc-llm.service",
    "sc-memory.service",
    "sc-speech.service",
    "sc.target",
}
MEMORY_MAX = {
    "sc-speech.service": "900M",
    "sc-memory.service": "512M",
    "sc-brain.service": "200M",
    "sc-gateway.service": "200M",
    "sc-buddy.service": "200M",
    "sc-compat.service": "200M",
    "sc-health.service": "200M",
}
BINDS = {
    "sc-gateway.service": ("0.0.0.0", "8443"),
    "sc-buddy.service": ("0.0.0.0", "8765"),
    "sc-memory.service": ("127.0.0.1", "8001"),
    "sc-speech.service": ("127.0.0.1", "8020"),
}


def build_probe_wheel(root: Path):
    """Minimal pure wheel plus a hashed lock, the WS3 artifact shape."""
    pkg = b'VALUE = "probe"\n'
    meta = (
        "Metadata-Version: 2.1\n"
        "Name: sc-release-probe\n"
        "Version: 1.0.0\n"
        "Summary: release probe\n"
    ).encode()
    wheel = (
        "Wheel-Version: 1.0\n"
        "Generator: test-release\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    ).encode()
    files = {
        "sc_release_probe/__init__.py": pkg,
        "sc_release_probe-1.0.0.dist-info/METADATA": meta,
        "sc_release_probe-1.0.0.dist-info/WHEEL": wheel,
    }

    def record_line(name, data):
        digest = hashlib.sha256(data).digest()
        encoded = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        return f"{name},sha256={encoded},{len(data)}\n"

    record = "".join(record_line(name, data) for name, data in files.items())
    record += "sc_release_probe-1.0.0.dist-info/RECORD,,\n"
    files["sc_release_probe-1.0.0.dist-info/RECORD"] = record.encode()
    wheelhouse = root / "wheelhouse"
    wheelhouse.mkdir(parents=True)
    whl = wheelhouse / "sc_release_probe-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    digest = hashlib.sha256(whl.read_bytes()).hexdigest()
    constraints = root / "constraints.lock"
    constraints.write_text(
        f"sc-release-probe==1.0.0 \\\n    --hash=sha256:{digest}\n"
    )
    return wheelhouse, constraints


def write_state(root: Path, *, sqlite_version=1, config_version=1, redis_version=1, note="keep-me"):
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "sc.sqlite")
    connection.execute("CREATE TABLE notes (body TEXT)")
    connection.execute("INSERT INTO notes (body) VALUES (?)", (note,))
    connection.execute(f"PRAGMA user_version = {int(sqlite_version)}")
    connection.commit()
    connection.close()
    config = root / "config" / "sc.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(f'config_version = {int(config_version)}\nname = "nanob"\n')
    redis_dir = root / "redis"
    redis_dir.mkdir(parents=True, exist_ok=True)
    (redis_dir / "schema_version").write_text(f"{int(redis_version)}\n")


def sqlite_version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()


def notes(path: Path):
    connection = sqlite3.connect(path)
    try:
        return [row[0] for row in connection.execute("SELECT body FROM notes ORDER BY body")]
    finally:
        connection.close()


def schema_v2(path: Path) -> None:
    path.write_text(json.dumps({
        "redis_key": "sc:schema_version",
        "redis_version": 2,
        "sqlite_user_version": 2,
        "config_version": 2,
        "sqlite": [
            "ALTER TABLE notes ADD COLUMN extra TEXT",
            "INSERT INTO notes (body, extra) VALUES ('new-only', 'x')",
        ],
        "config": {"config_version": 2},
    }))


def write_backup_tool(path: Path, log: Path) -> None:
    path.write_text(f'''
import argparse, json, shutil, sqlite3, sys
from pathlib import Path
LOG = Path({str(log)!r})

def version_of(db):
    if not db.exists():
        return 0
    con = sqlite3.connect(db)
    try:
        return int(con.execute("PRAGMA user_version").fetchone()[0])
    finally:
        con.close()

def copy_tree(src, dest):
    mapping = {{
        "sqlite": (src / "sc.sqlite", dest / "sqlite" / "sc.sqlite"),
        "config": (src / "config" / "sc.toml", dest / "config" / "sc.toml"),
        "redis": (src / "redis" / "schema_version", dest / "redis" / "schema_version"),
    }}
    present = {{}}
    for name, (source, target) in mapping.items():
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            present[name] = str(target.relative_to(dest))
    return present

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    snap = sub.add_parser("snapshot")
    for cmd in (snap, sub.add_parser("restore")):
        cmd.add_argument("--dest", dest="location")
        cmd.add_argument("--snapshot", dest="location")
        cmd.add_argument("--state-root", required=True)
        cmd.add_argument("--components", required=True)
        cmd.add_argument("--redis-key", required=True)
        cmd.add_argument("--redis-url", default="file")
    args = parser.parse_args()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as handle:
        handle.write(" ".join(sys.argv[1:]) + "\\n")
        if args.cmd == "snapshot":
            handle.write("snapshot user_version=%s\\n" % version_of(Path(args.state_root) / "sc.sqlite"))
    if args.components != "sqlite,redis,config":
        sys.exit("bad components")
    if args.redis_key != "sc:schema_version":
        sys.exit("bad redis key")
    state = Path(args.state_root)
    location = Path(args.location)
    if args.cmd == "snapshot":
        location.mkdir(parents=True, exist_ok=True)
        present = copy_tree(state, location)
        manifest = {{
            "producer": "ws1",
            "redis_key": args.redis_key,
            "schema": {{
                "redis": int((state / "redis" / "schema_version").read_text()),
                "sqlite": version_of(state / "sc.sqlite"),
                "config": 1,
            }},
            "files": present,
        }}
        (location / "manifest.json").write_text(json.dumps(manifest))
        return
    present = copy_tree(location, state) if False else None
    mapping = {{
        "sqlite": (location / "sqlite" / "sc.sqlite", state / "sc.sqlite"),
        "config": (location / "config" / "sc.toml", state / "config" / "sc.toml"),
        "redis": (location / "redis" / "schema_version", state / "redis" / "schema_version"),
    }}
    for source, target in mapping.values():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (state / "RESTORED_BY_WS1").write_text("ws1\\n")

if __name__ == "__main__":
    main()
''')


class UnitAndPolicyTests(unittest.TestCase):
    def test_unit_names_memory_caps_ports_and_partof(self):
        found = {path.name for path in UNITS.iterdir() if path.is_file()}
        self.assertEqual(found, EXPECTED_UNITS)
        self.assertFalse((UNITS / "orin-voice.service").exists())
        for name in EXPECTED_UNITS:
            text = (UNITS / name).read_text()
            self.assertNotIn("orin-voice", text)
            self.assertNotIn("sentient-", text)
            self.assertNotIn("ollama.service", text)
        for name, cap in MEMORY_MAX.items():
            text = (UNITS / name).read_text()
            self.assertIn("PartOf=sc.target\n", text)
            self.assertIn("WantedBy=sc.target\n", text)
            self.assertIn(f"MemoryMax={cap}\n", text)
            self.assertIn("User=sc\n", text)
            self.assertIn("WorkingDirectory=/opt/sc/current\n", text)
            self.assertIn("/opt/sc/current/venv/bin/python", text)
        for name, (bind, port) in BINDS.items():
            text = (UNITS / name).read_text()
            self.assertIn(f"Environment=SC_BIND={bind}\n", text)
            self.assertIn(f"Environment=SC_PORT={port}\n", text)
        llm = (UNITS / "sc-llm.service").read_text()
        self.assertIn("PartOf=sc.target\n", llm)
        self.assertIn("WantedBy=sc.target\n", llm)
        self.assertNotIn("MemoryMax=", llm)
        self.assertIn("--memory=3300m", llm)
        self.assertIn("--memory-swap=3300m", llm)
        self.assertIn("127.0.0.1:18080:8080", llm)
        self.assertNotIn("0.0.0.0:", llm)
        target = (UNITS / "sc.target").read_text()
        self.assertNotIn("PartOf=", target)
        self.assertIn("WantedBy=multi-user.target\n", target)
        self.assertNotIn("Wants=sc-", target)
        speech = (UNITS / "sc-speech.service").read_text()
        self.assertIn("sentient.services.voice", speech)
        self.assertIn("SupplementaryGroups=audio", speech)

    def test_systemd_analyze_verify(self):
        binary = shutil.which("systemd-analyze")
        self.assertIsNotNone(binary, "systemd-analyze is required to verify unit files")
        files = sorted(str(path) for path in UNITS.iterdir() if path.is_file())
        proc = subprocess.run(
            [binary, "verify", *files],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_polkit_restarts_exact_units_without_wildcards(self):
        text = POLKIT.read_text()
        self.assertNotIn("*", text)
        self.assertNotIn("?", text)
        self.assertNotRegex(text, r"startsWith|RegExp|indexOf|match\(|\.test\(")
        literals = re_units(text)
        self.assertEqual(literals, EXPECTED_UNITS)
        self.assertEqual(text.count("polkit.Result.YES"), 1)
        self.assertIn('"restart"', text)
        for verb in ("start", "stop", "enable", "disable", "mask"):
            self.assertNotIn(f'"{verb}"', text)
        self.assertIn('subject.isInGroup("sc")', text)
        self.assertEqual(release.exact_restart_units(), EXPECTED_UNITS)

    def test_restart_and_enable_reject_wildcards_and_legacy_units(self):
        calls = []
        argv = release.restart_unit("sc-brain.service", systemctl=calls.append)
        self.assertEqual(argv, ["systemctl", "restart", "sc-brain.service"])
        self.assertEqual(calls, [argv])
        for name in ("sc-*.service", "sc-brain.*", "orin-voice.service",
                     "sentient-voice.service", "sc-brain.service;reboot",
                     "sc-brain.service sc-speech.service", "../sc-brain.service"):
            with self.subTest(name=name):
                with self.assertRaises(release.ReleaseError):
                    release.restart_unit(name, systemctl=calls.append)
                with self.assertRaises(release.ReleaseError):
                    release.enable_unit(name, active=set(), systemctl=calls.append)
        self.assertEqual(len(calls), 1)

    def test_enable_refuses_active_legacy_and_does_not_stop_it(self):
        calls = []
        with self.assertRaises(release.ReleaseError) as caught:
            release.enable_unit(
                "sc-speech.service",
                active={"orin-voice.service", "mosquitto.service"},
                systemctl=calls.append,
            )
        self.assertIn("orin-voice.service", str(caught.exception))
        self.assertEqual(calls, [])
        argv = release.enable_unit(
            "sc-health.service",
            active={"orin-voice.service", "ollama.service"},
            systemctl=calls.append,
        )
        self.assertEqual(argv, ["systemctl", "enable", "sc-health.service"])
        self.assertNotIn("--now", argv)
        with self.assertRaises(release.ReleaseError):
            release.enable_unit(
                "sc.target",
                active={"sentient-core.target"},
                systemctl=calls.append,
            )

    def test_install_units_copies_files_without_enabling_or_following_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "systemd"
            calls = []
            planned = release.install_units(dest, dry_run=True, systemctl=calls.append)
            self.assertEqual(set(planned), EXPECTED_UNITS)
            self.assertFalse(dest.exists())
            self.assertEqual(calls, [])
            written = release.install_units(dest, systemctl=calls.append)
            self.assertEqual(set(written), EXPECTED_UNITS)
            self.assertEqual(calls, [])
            self.assertFalse(any(dest.rglob("*.wants")))
            self.assertEqual((dest / "sc-brain.service").read_bytes(), (UNITS / "sc-brain.service").read_bytes())
            polkit_dest = Path(tmp) / "polkit"
            release.install_units(dest, polkit_dest=polkit_dest)
            self.assertEqual((polkit_dest / POLKIT.name).read_bytes(), POLKIT.read_bytes())
            legacy = Path(tmp) / "legacy" / "sc-brain.service"
            legacy.parent.mkdir()
            legacy.symlink_to(dest / "sc-memory.service")
            with self.assertRaises(release.ReleaseError):
                release.install_units(legacy.parent)

    def test_power_modes_and_confirm(self):
        self.assertEqual(release.POWER_MODES, {"15W": 0, "25W": 1, "MAXN_SUPER": 2})
        self.assertEqual(release.SOAK_POWER_MODE, 1)
        self.assertEqual(release.parse_nvpmodel_query("NV Power Mode: MAXN_SUPER\n2\n"), 2)
        self.assertEqual(release.parse_nvpmodel_query("Power Mode: 25W\n"), 1)
        self.assertEqual(release.parse_nvpmodel_query("NV Power Mode: MODE_15W\n0\n"), 0)
        calls = []
        with self.assertRaises(release.ReleaseError):
            release.set_power_mode("25W", confirm=False, runner=calls.append)
        self.assertEqual(calls, [])
        argv = release.set_power_mode("MAXN_SUPER", confirm=True, runner=calls.append)
        self.assertEqual(argv, ["nvpmodel", "-m", "2"])
        self.assertEqual(calls, [argv])

    def test_ws3_artifact_paths_and_backup_tool_resolution(self):
        self.assertEqual(release.WS3_WHEELHOUSE_RELATIVE, Path("dist/wheelhouse"))
        self.assertEqual(release.WS3_CONSTRAINTS_RELATIVE, Path("constraints.lock"))
        self.assertEqual(release.WS3_BUILD_SCRIPT, Path("deploy/build-wheels.sh"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(release.resolve_backup_tool("auto", [root]))
            self.assertIsNone(release.resolve_backup_tool("builtin", [root]))
            tool = root / "tools" / "orin_backup.py"
            tool.parent.mkdir(parents=True)
            tool.write_text("print('ws1')\n")
            self.assertEqual(release.resolve_backup_tool("auto", [root]), tool)
            with self.assertRaises(release.ReleaseError):
                release.resolve_backup_tool(str(root / "missing.py"), [root])

    def test_constraint_hash_and_version_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "constraints.lock"
            path.write_text("# lock\nfoo==1.0 \\\n    --hash=sha256:" + ("ab" * 32) + "\n")
            release.assert_constraints_hashed(path)
            path.write_text("foo==1.0\n")
            with self.assertRaises(release.ReleaseError):
                release.assert_constraints_hashed(path)
        for version in ("", ".", "..", "../etc", "1.0/evil", "sc-*", "v 1"):
            with self.subTest(version=version):
                with self.assertRaises(release.ReleaseError):
                    release.require_version(version)
        self.assertEqual(release.require_version("1.2.3+githash"), "1.2.3+githash")


class RedisAndHealthTests(unittest.TestCase):
    def test_redis_schema_roundtrip_over_resp(self):
        server = FakeRedis()
        server.start()
        server.ready.wait(2)
        url = f"redis://127.0.0.1:{server.port}/0"
        client = release.RedisSchema(url, Path("/unused"), "sc:schema_version")
        self.assertIsNone(client.get())
        client.set(4)
        self.assertEqual(client.get(), 4)
        client.delete()
        self.assertIsNone(client.get())
        client.close()
        server.stop()

    def test_airi_turn_requires_a_nonempty_completion(self):
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                seen["body"] = body
                seen["auth"] = self.headers.get("Authorization")
                content = "" if self.path == "/empty" else "pong"
                status = 401 if self.path == "/denied" else 200
                payload = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, fmt, *args):
                return

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            text = release.airi_turn(base + "/v1/chat/completions", token="secret", prompt="ping", timeout=2)
            self.assertEqual(text, "pong")
            self.assertEqual(seen["auth"], "Bearer secret")
            self.assertEqual(seen["body"]["messages"][0]["content"], "ping")
            with self.assertRaises(release.ReleaseError):
                release.airi_turn(base + "/empty", token=None, prompt="ping", timeout=2)
            with self.assertRaises(release.ReleaseError):
                release.airi_turn(base + "/denied", token=None, prompt="ping", timeout=2)
        finally:
            httpd.shutdown()
            httpd.server_close()


class ReleaseFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.wheelhouse, cls.constraints = build_probe_wheel(Path(cls.tmp.name) / "ws3")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def work(self):
        base = Path(self.tmp.name) / self.id().split(".")[-1]
        if base.exists():
            shutil.rmtree(base)
        prefix = base / "opt" / "sc"
        state = base / "var" / "lib" / "sc"
        prefix.mkdir(parents=True)
        return prefix, state

    def spec(self, prefix, state, version, **kwargs):
        values = dict(
            version=version,
            prefix=prefix,
            state_root=state,
            wheelhouse=self.wheelhouse,
            constraints=self.constraints,
            redis_url=None,
            backup_tool=None,
            health_fn=lambda: None,
        )
        values.update(kwargs)
        return release.DeploySpec(**values)

    def test_pip_command_uses_target_venv_and_hashed_offline_install(self):
        command = release.pip_install_command(
            Path("/opt/sc/releases/1.0.0/venv"),
            Path("dist/wheelhouse"),
            Path("constraints.lock"),
        )
        self.assertEqual(command[0], "/opt/sc/releases/1.0.0/venv/bin/python")
        self.assertEqual(command[1:4], ["-m", "pip", "install"])
        self.assertIn("--no-index", command)
        self.assertIn("--require-hashes", command)
        self.assertIn("--find-links", command)
        self.assertEqual(command[command.index("--find-links") + 1], "dist/wheelhouse")
        self.assertIn("-r", command)
        self.assertEqual(command[command.index("-r") + 1], "constraints.lock")

    def test_deploy_creates_a_new_venv_and_switches_current_atomically(self):
        prefix, state = self.work()
        wheelhouse = prefix.parent / "wheelhouse"
        shutil.copytree(self.wheelhouse, wheelhouse)
        decoy = wheelhouse / "venv"
        decoy.mkdir()
        (decoy / "copied-marker").write_text("from-another-machine\n")
        (decoy / "pyvenv.cfg").write_text("home = /copied/from/build/machine\n")
        result = release.deploy(self.spec(prefix, state, "1.0.0", wheelhouse=wheelhouse))
        link = prefix / "current"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "releases/1.0.0")
        self.assertEqual(result.version, "1.0.0")
        venv = prefix / "releases" / "1.0.0" / "venv"
        self.assertFalse((venv / "copied-marker").exists())
        config = (venv / "pyvenv.cfg").read_text()
        self.assertIn("-m venv", config)
        self.assertIn(str(venv), config)
        self.assertNotIn("copied/from/build/machine", config)
        imported = subprocess.run(
            [str(venv / "bin" / "python"), "-c", "import sc_release_probe; print(sc_release_probe.VALUE)"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertEqual(imported.stdout.strip(), "probe")
        with self.assertRaises(release.ReleaseError):
            release.deploy(self.spec(prefix, state, "1.0.0"))

    def test_schema_change_health_failure_restores_ws1_snapshot_then_symlink(self):
        prefix, state = self.work()
        write_state(state)
        tool_log = prefix.parent / "ws1.log"
        tool = prefix.parent / "orin_backup.py"
        write_backup_tool(tool, tool_log)
        schema = prefix.parent / "schema-v1.json"
        schema.write_text(json.dumps({
            "redis_version": 1,
            "sqlite_user_version": 1,
            "config_version": 1,
        }))
        release.deploy(self.spec(prefix, state, "1.0.0", schema_path=schema, backup_tool=tool))
        self.assertFalse(tool_log.exists())
        schema2 = prefix.parent / "schema-v2.json"
        schema_v2(schema2)
        seen = {}

        def health():
            seen["version"] = sqlite_version(state / "sc.sqlite")
            seen["notes"] = notes(state / "sc.sqlite")
            seen["current"] = os.readlink(prefix / "current")
            seen["config"] = (state / "config" / "sc.toml").read_text()
            raise release.ReleaseError("AIRI turn failed")

        with self.assertRaises(release.DeployRolledBack) as caught:
            release.deploy(self.spec(
                prefix, state, "2.0.0",
                schema_path=schema2,
                backup_tool=tool,
                health_fn=health,
            ))
        self.assertEqual(seen["version"], 2)
        self.assertIn("new-only", seen["notes"])
        self.assertEqual(seen["current"], "releases/2.0.0")
        self.assertIn("config_version = 2", seen["config"])
        self.assertEqual(os.readlink(prefix / "current"), "releases/1.0.0")
        self.assertEqual(sqlite_version(state / "sc.sqlite"), 1)
        self.assertEqual(notes(state / "sc.sqlite"), ["keep-me"])
        self.assertIn('config_version = 1', (state / "config" / "sc.toml").read_text())
        self.assertIn('name = "nanob"', (state / "config" / "sc.toml").read_text())
        self.assertEqual((state / "redis" / "schema_version").read_text().strip(), "1")
        self.assertEqual((state / "RESTORED_BY_WS1").read_text().strip(), "ws1")
        log = tool_log.read_text().splitlines()
        self.assertTrue(any("--components sqlite,redis,config" in line for line in log))
        self.assertTrue(any("--redis-key sc:schema_version" in line for line in log))
        snap_at = next(index for index, line in enumerate(log) if line == "snapshot user_version=1")
        restore_at = next(index for index, line in enumerate(log) if line.startswith("restore "))
        self.assertLess(snap_at, restore_at)
        self.assertNotIn("snapshot user_version=2", log)
        self.assertEqual(caught.exception.restored_to, "1.0.0")
        state_doc = json.loads((prefix / "deploy-state.json").read_text())
        self.assertEqual(state_doc["current"], "1.0.0")
        self.assertEqual(state_doc["abandoned"], "2.0.0")
        self.assertTrue((prefix / "releases" / "2.0.0" / "venv" / "bin" / "python").exists())

    def test_code_only_health_failure_does_not_touch_state(self):
        prefix, state = self.work()
        write_state(state, note="stable")
        digest_before = hashlib.sha256((state / "sc.sqlite").read_bytes()).hexdigest()
        release.deploy(self.spec(prefix, state, "1.0.0"))

        def fail():
            raise release.ReleaseError("down")

        with self.assertRaises(release.DeployRolledBack):
            release.deploy(self.spec(prefix, state, "1.0.1", health_fn=fail))
        self.assertEqual(os.readlink(prefix / "current"), "releases/1.0.0")
        self.assertEqual(hashlib.sha256((state / "sc.sqlite").read_bytes()).hexdigest(), digest_before)
        self.assertEqual(notes(state / "sc.sqlite"), ["stable"])
        self.assertFalse((state / "snapshots").exists())
        self.assertFalse((state / "RESTORED_BY_WS1").exists())

    def test_successful_schema_deploy_keeps_release_and_http_turn(self):
        prefix, state = self.work()
        write_state(state)
        schema = prefix.parent / "schema-v2.json"
        schema_v2(schema)
        served = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                served["body"] = json.loads(self.rfile.read(length))
                payload = json.dumps({"choices": [{"message": {"content": "pong"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, fmt, *args):
                return

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            release.deploy(self.spec(prefix, state, "1.0.0"))
            result = release.deploy(self.spec(
                prefix, state, "2.0.0",
                schema_path=schema,
                health_fn=None,
                health_url=f"http://127.0.0.1:{httpd.server_address[1]}/v1/chat/completions",
                health_token="tok",
                health_prompt="ping",
                health_timeout=2,
            ))
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertEqual(result.version, "2.0.0")
        self.assertEqual(os.readlink(prefix / "current"), "releases/2.0.0")
        self.assertEqual(notes(state / "sc.sqlite"), ["keep-me", "new-only"])
        self.assertEqual(sqlite_version(state / "sc.sqlite"), 2)
        self.assertEqual((state / "redis" / "schema_version").read_text().strip(), "2")
        self.assertIn("config_version = 2", (state / "config" / "sc.toml").read_text())
        self.assertIn('name = "nanob"', (state / "config" / "sc.toml").read_text())
        self.assertEqual(served["body"]["messages"][0]["content"], "ping")
        manual = release.rollback(release.RollbackSpec(prefix=prefix, state_root=state, redis_url=None))
        self.assertEqual(manual, "1.0.0")
        self.assertEqual(os.readlink(prefix / "current"), "releases/1.0.0")
        self.assertEqual(notes(state / "sc.sqlite"), ["keep-me"])
        self.assertEqual(sqlite_version(state / "sc.sqlite"), 1)

    def test_snapshot_failure_and_downgrade_leave_current_release(self):
        prefix, state = self.work()
        write_state(state)
        release.deploy(self.spec(prefix, state, "1.0.0"))
        digest = hashlib.sha256((state / "sc.sqlite").read_bytes()).hexdigest()
        tool = prefix.parent / "broken_backup.py"
        tool.write_text("import sys\nsys.stderr.write('backup refused\\n')\nsys.exit(7)\n")
        schema = prefix.parent / "schema-v2.json"
        schema_v2(schema)
        with self.assertRaises(release.ReleaseError):
            release.deploy(self.spec(prefix, state, "2.0.0", schema_path=schema, backup_tool=tool))
        self.assertEqual(os.readlink(prefix / "current"), "releases/1.0.0")
        self.assertEqual(hashlib.sha256((state / "sc.sqlite").read_bytes()).hexdigest(), digest)
        self.assertFalse((prefix / "releases" / "2.0.0").exists())
        higher = prefix.parent / "schema-9.json"
        higher.write_text(json.dumps({
            "redis_version": 9,
            "sqlite_user_version": 9,
            "config_version": 9,
        }))
        release.deploy(self.spec(prefix, state, "9.0.0", schema_path=higher, health_fn=lambda: None))
        older = prefix.parent / "schema-1.json"
        older.write_text(json.dumps({
            "redis_version": 1,
            "sqlite_user_version": 1,
            "config_version": 1,
        }))
        with self.assertRaises(release.ReleaseError):
            release.deploy(self.spec(prefix, state, "9.0.1", schema_path=older))
        self.assertEqual(os.readlink(prefix / "current"), "releases/9.0.0")
        self.assertEqual(sqlite_version(state / "sc.sqlite"), 9)

    def test_first_deploy_health_failure_removes_current_and_restores_empty_state(self):
        prefix, state = self.work()
        state.mkdir(parents=True)
        schema = prefix.parent / "schema.json"
        schema.write_text(json.dumps({
            "redis_version": 1,
            "sqlite_user_version": 1,
            "config_version": 1,
            "sqlite": [
                "CREATE TABLE notes (body TEXT)",
                "INSERT INTO notes (body) VALUES ('created')",
            ],
            "config": {"config_version": 1, "name": "nanob"},
        }))
        seen = {}

        def health():
            seen["exists"] = (state / "sc.sqlite").exists()
            seen["current"] = os.readlink(prefix / "current")
            raise release.ReleaseError("no airi")

        with self.assertRaises(release.DeployRolledBack) as caught:
            release.deploy(self.spec(prefix, state, "0.1.0", schema_path=schema, health_fn=health))
        self.assertTrue(seen["exists"])
        self.assertEqual(seen["current"], "releases/0.1.0")
        self.assertFalse((prefix / "current").exists())
        self.assertFalse((state / "sc.sqlite").exists())
        self.assertFalse((state / "config" / "sc.toml").exists())
        self.assertFalse((state / "redis" / "schema_version").exists())
        self.assertIsNone(caught.exception.restored_to)

    def test_tampered_snapshot_is_not_restored(self):
        prefix, state = self.work()
        write_state(state)
        snap = release.snapshot_state(state, state / "snapshots" / "clean", redis_url=None)
        original = (state / "sc.sqlite").read_bytes()
        db = snap / "sqlite" / "sc.sqlite"
        db.write_bytes(db.read_bytes() + b"tamper")
        with self.assertRaises(release.ReleaseError):
            release.restore_snapshot(state, snap, redis_url=None)
        self.assertEqual((state / "sc.sqlite").read_bytes(), original)

    def test_cli_deploy_rollback_and_help(self):
        prefix, state = self.work()
        write_state(state)
        schema = prefix.parent / "schema-v2.json"
        schema_v2(schema)
        cli = [sys.executable, str(ROOT / "deploy" / "sc-ctl")]
        help_proc = subprocess.run([*cli, "deploy", "--help"], check=False, capture_output=True, text=True)
        self.assertEqual(help_proc.returncode, 0, help_proc.stderr)
        self.assertIn("dist/wheelhouse", help_proc.stdout)
        self.assertIn("constraints.lock", help_proc.stdout)
        self.assertIn("build-wheels.sh", help_proc.stdout)
        common = [
            "--prefix", str(prefix),
            "--state-root", str(state),
            "--wheelhouse", str(self.wheelhouse),
            "--constraints", str(self.constraints),
            "--redis-url", "file",
            "--backup-tool", "builtin",
        ]
        first = subprocess.run(
            [*cli, "deploy", "--version", "1.0.0", *common, "--health-cmd", "/bin/true"],
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        second = subprocess.run(
            [*cli, "deploy", "--version", "2.0.0", *common, "--schema", str(schema), "--health-cmd", "/bin/false"],
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(second.returncode, 3, second.stderr)
        self.assertIn("ROLLBACK", second.stderr)
        self.assertEqual(os.readlink(prefix / "current"), "releases/1.0.0")
        self.assertEqual(notes(state / "sc.sqlite"), ["keep-me"])
        status = subprocess.run(
            [*cli, "status", "--prefix", str(prefix), "--state-root", str(state), "--redis-url", "file", "--json"],
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        payload = json.loads(status.stdout)
        self.assertEqual(payload["current"], "1.0.0")
        self.assertEqual(payload["schema"]["sqlite"], 1)
        power = subprocess.run([*cli, "power", "set", "25W"], check=False, capture_output=True, text=True)
        self.assertEqual(power.returncode, 1)
        self.assertIn("confirm", power.stderr.lower())
        power_help = subprocess.run([*cli, "power", "--help"], check=False, capture_output=True, text=True)
        self.assertIn("MAXN_SUPER", power_help.stdout)
        self.assertIn("15W", power_help.stdout)


class FakeRedis(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        self.socket.listen(1)
        self.ready = threading.Event()
        self.store = {}
        self._halt = False

    def run(self):
        self.ready.set()
        self.socket.settimeout(0.5)
        conn = None
        while conn is None and not self._halt:
            try:
                conn, _addr = self.socket.accept()
            except socket.timeout:
                continue
        if conn is None:
            return
        conn.settimeout(0.5)
        buffer = b""
        while not self._halt:
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            buffer += chunk
            while True:
                command, buffer = pop_resp(buffer)
                if command is None:
                    break
                name = command[0].upper()
                if name == "GET":
                    value = self.store.get(command[1])
                    if value is None:
                        conn.sendall(b"$-1\r\n")
                    else:
                        payload = value.encode()
                        conn.sendall(b"$" + str(len(payload)).encode() + b"\r\n" + payload + b"\r\n")
                elif name == "SET":
                    self.store[command[1]] = command[2]
                    conn.sendall(b"+OK\r\n")
                elif name == "DEL":
                    self.store.pop(command[1], None)
                    conn.sendall(b":1\r\n")
                else:
                    conn.sendall(b"-ERR\r\n")
        conn.close()

    def stop(self):
        self._halt = True
        self.join(timeout=2)
        self.socket.close()


def pop_resp(buffer):
    if not buffer.startswith(b"*"):
        return None, buffer
    try:
        header, rest = buffer.split(b"\r\n", 1)
        count = int(header[1:])
    except ValueError:
        return None, buffer
    parts = []
    for _ in range(count):
        if not rest.startswith(b"$"):
            return None, buffer
        try:
            size_text, rest = rest.split(b"\r\n", 1)
            size = int(size_text[1:])
        except ValueError:
            return None, buffer
        if len(rest) < size + 2:
            return None, buffer
        parts.append(rest[:size].decode())
        rest = rest[size + 2:]
    return parts, rest


def re_units(text):
    import re
    return set(re.findall(r'"(sc(?:-[a-z0-9]+)*\.(?:service|target))"', text))


if __name__ == "__main__":
    unittest.main()
