from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

from sc.auth import AuthError, authenticate, load_tokens, parse_tokens
from sc.config import Settings, load_settings


def tok():
    return secrets.token_urlsafe(32)


def test_parse_tokens_and_match():
    a, b = tok(), tok()
    store = parse_tokens(f"# comment\njack chat {a}\n\nvoice chat+speaker {b}\n")
    assert authenticate(store, f"Bearer {a}").user == "jack"
    assert authenticate(store, f"bearer {b}").can("speaker")
    assert store.users == {"jack"}
    with pytest.raises(AuthError):
        authenticate(store, "Bearer " + tok())
    with pytest.raises(AuthError):
        authenticate(store, None)


@pytest.mark.parametrize("line", [
    "jack chat short",
    "Jack chat " + "a" * 40,
    "jack root " + "a" * 40,
    "jack chat",
    "jack chat " + "a" * 40 + " extra",
])
def test_bad_token_lines_rejected(line):
    with pytest.raises(ValueError):
        parse_tokens(line)


def test_duplicate_and_empty_rejected():
    t = tok()
    with pytest.raises(ValueError, match="duplicate"):
        parse_tokens(f"jack chat {t}\npartner chat {t}")
    with pytest.raises(ValueError):
        parse_tokens("# nothing\n")


def test_token_file_permissions(tmp_path):
    p = tmp_path / "tokens"
    p.write_text(f"jack chat {tok()}\n")
    os.chmod(p, 0o644)
    with pytest.raises(PermissionError):
        load_tokens(p)
    os.chmod(p, 0o600)
    assert load_tokens(p).users == {"jack"}
    os.chmod(p, 0o640)
    assert load_tokens(p).users == {"jack"}


def test_settings_file_then_env(tmp_path):
    cfg = tmp_path / "sc.toml"
    cfg.write_text('bind_port = 9000\nllm_url = "http://127.0.0.1:1"\ncors_origins = ["http://phone.lan:5173"]\n')
    s = load_settings(env={"SC_CONFIG": str(cfg), "SC_BIND_PORT": "9100", "SC_TEMPERATURE": "0.2"})
    assert s.bind_port == 9100 and s.llm_url == "http://127.0.0.1:1"
    assert s.cors_origins == ("http://phone.lan:5173",) and s.temperature == 0.2
    assert not s.lan_exposed


def test_settings_reject_unknown_and_bad_values(tmp_path):
    cfg = tmp_path / "sc.toml"
    cfg.write_text("bind_prot = 1\n")
    with pytest.raises(ValueError, match="unknown"):
        load_settings(env={"SC_CONFIG": str(cfg)})
    with pytest.raises(ValueError):
        load_settings(env={"SC_CONFIG": str(tmp_path / "none.toml"), "SC_CORS_ORIGINS": "*"})
    with pytest.raises(ValueError):
        load_settings(env={"SC_CONFIG": str(tmp_path / "none.toml"), "SC_VOICE_QUEUE_S": "0"})


def test_default_persona_exists_and_is_compact():
    p = Settings().persona_path
    assert Path(p).is_file()
    assert len(Path(p).read_text()) < 4000, "keep the persona small: it is prefilled on every uncached turn"


def test_lan_bind_requires_tls_or_explicit_opt_in(tmp_path):
    none = str(tmp_path / "none.toml")
    with pytest.raises(ValueError, match="beyond loopback"):
        load_settings(env={"SC_CONFIG": none, "SC_BIND_HOST": "0.0.0.0"})
    s = load_settings(env={"SC_CONFIG": none, "SC_BIND_HOST": "0.0.0.0", "SC_ALLOW_PLAIN_LAN": "true"})
    assert s.lan_exposed and s.allow_plain_lan
    with pytest.raises(ValueError, match="both"):
        load_settings(env={"SC_CONFIG": none, "SC_TLS_CERT": "/x.crt"})
    s = load_settings(env={"SC_CONFIG": none, "SC_BIND_HOST": "0.0.0.0",
                           "SC_TLS_CERT": "/x.crt", "SC_TLS_KEY": "/x.key"})
    assert s.tls_cert == Path("/x.crt")
    assert not load_settings(env={"SC_CONFIG": none}).lan_exposed


def test_sc_core_serves_https_with_local_ca(tmp_path):
    """Real process, real TLS: a client that trusts only the local CA can talk;
    plain HTTP to the same port fails."""
    import shutil
    import socket
    import subprocess
    import sys
    import time

    import httpx

    if shutil.which("openssl") is None:
        pytest.skip("openssl missing")
    repo = Path(__file__).resolve().parents[2]
    tls = tmp_path / "tls"
    env = {**os.environ, "SC_TLS_DIR": str(tls)}
    if os.geteuid() != 0:
        pytest.skip("make-cert.sh needs root")
    subprocess.run(["bash", str(repo / "deploy/v3/make-cert.sh"), "127.0.0.1", "localhost"],
                   check=True, env=env, capture_output=True)
    (tmp_path / "tokens").write_text(f"jack chat {tok()}\n")
    os.chmod(tmp_path / "tokens", 0o600)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (tmp_path / "sc.toml").write_text(
        f'bind_host = "0.0.0.0"\nbind_port = {port}\nllm_url = "http://127.0.0.1:9"\n'
        f'db_path = "{tmp_path / "sc.db"}"\ntokens_file = "{tmp_path / "tokens"}"\n'
        f'tls_cert = "{tls / "server.crt"}"\ntls_key = "{tls / "server.key"}"\n')
    proc = subprocess.Popen([sys.executable, "-m", "sc.core"], cwd=repo,
                            env={**os.environ, "SC_CONFIG": str(tmp_path / "sc.toml"), "PYTHONPATH": str(repo)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        body = None
        for _ in range(100):
            try:
                r = httpx.get(f"https://127.0.0.1:{port}/healthz", verify=str(tls / "ca.crt"), timeout=2)
                body = r.json()
                break
            except httpx.ConnectError:
                time.sleep(0.1)
        assert body == {"status": "degraded"}, "LAN-exposed healthz must be redacted and the LLM is down"
        with pytest.raises(httpx.HTTPError):
            httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=2)
        with pytest.raises(httpx.ConnectError):
            httpx.get(f"https://127.0.0.1:{port}/healthz", timeout=2)  # untrusted without the CA
    finally:
        proc.terminate()
        proc.wait(10)


def test_lan_bind_without_tls_refuses_to_start(tmp_path):
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[2]
    (tmp_path / "tokens").write_text(f"jack chat {tok()}\n")
    os.chmod(tmp_path / "tokens", 0o600)
    (tmp_path / "sc.toml").write_text(f'bind_host = "0.0.0.0"\ntokens_file = "{tmp_path / "tokens"}"\n')
    r = subprocess.run([sys.executable, "-m", "sc.core"], check=False, cwd=repo, capture_output=True, text=True,
                       timeout=60,
                       env={**os.environ, "SC_CONFIG": str(tmp_path / "sc.toml"), "PYTHONPATH": str(repo)})
    assert r.returncode == 78 and "beyond loopback" in r.stderr
