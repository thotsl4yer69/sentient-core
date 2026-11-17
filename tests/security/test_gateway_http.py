"""HTTP 401 and 403 behaviour for the API-only gateway."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentient.services.gateway.app import create_app
from sentient.services.gateway.config import ConfigError, GatewayConfig
from sentient.services.gateway.policy import ALLOWED_JOB_TYPES, classify_text

CHAT = "c" * 24 + "chat"
CONTROL = "k" * 24 + "ctrl"
REPO = Path(__file__).resolve().parents[2]


def _env(**overrides: str) -> dict[str, str]:
    base = {
        "SC_GATEWAY_TOKENS": f"chat:{CHAT},control:{CONTROL}",
        "SC_GATEWAY_MQTT": "0",
    }
    base.update(overrides)
    return base


def _app(env: dict[str, str]):
    return create_app(GatewayConfig.from_env(env))


class _Recorder(ThreadingHTTPServer):
    def __init__(self, server_address, request_handler_class):
        super().__init__(server_address, request_handler_class)
        self.requests: list[dict] = []


class _LLM(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):  # noqa: N802 - stdlib handler name
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        body = json.loads(raw)
        self.server.requests.append(body)  # type: ignore[attr-defined]
        if body.get("stream"):
            payload = (
                'data: {"choices":[{"index":0,"delta":{"content":"hello"}}]}\n\n'
                "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        payload = json.dumps(
            {
                "id": "local",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hello from llm"},
                        "finish_reason": "stop",
                    }
                ],
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        return


@pytest.fixture()
def llm_server():
    server = _Recorder(("127.0.0.1", 0), _LLM)
    thread_started = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    thread_started.start()
    port = server.server_address[1]
    try:
        yield server, f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()


def test_allowlist_is_exact():
    assert ALLOWED_JOB_TYPES == frozenset({"status.health", "status.avatar", "turn.cancel"})


def test_gateway_package_does_not_spawn_processes():
    root = REPO / "sentient" / "services" / "gateway"
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "import subprocess" not in text
        assert "os.system" not in text
        assert "create_subprocess" not in text


def test_default_bind_is_loopback_and_requires_a_real_token():
    with pytest.raises(ConfigError, match="SC_GATEWAY_TOKENS"):
        GatewayConfig.from_env({"SC_GATEWAY_MQTT": "0"})
    with pytest.raises(ConfigError, match="MQTT"):
        GatewayConfig.from_env({"SC_GATEWAY_TOKENS": f"chat:{CHAT}"})
    config = GatewayConfig.from_env(_env())
    assert config.host == "127.0.0.1"
    assert config.port == 8443
    assert config.bind_lan is False
    assert config.tls_cert is None


def test_lan_and_public_bind_are_refused_without_tls(tmp_path: Path):
    with pytest.raises(ConfigError, match="LAN bind requires"):
        GatewayConfig.from_env(_env(SC_GATEWAY_BIND_LAN="1"))
    with pytest.raises(ConfigError, match="non-loopback"):
        GatewayConfig.from_env(_env(SC_GATEWAY_BIND="0.0.0.0"))
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    config = GatewayConfig.from_env(
        _env(
            SC_GATEWAY_BIND_LAN="1",
            SC_GATEWAY_BIND="192.168.1.10",
            SC_GATEWAY_TLS_CERT=str(cert),
            SC_GATEWAY_TLS_KEY=str(key),
        )
    )
    assert config.host == "192.168.1.10"
    assert config.tls_cert == str(cert)


def test_mqtt_password_does_not_leave_loopback_in_plaintext():
    with pytest.raises(ConfigError, match="1883"):
        GatewayConfig.from_env(
            _env(
                SC_GATEWAY_MQTT="1",
                SC_MQTT_HOST="192.168.1.159",
                SC_MQTT_PORT="1883",
                SC_MQTT_PASS="a" * 24,
            )
        )


def test_token_file_must_be_private(tmp_path: Path):
    path = tmp_path / "tokens"
    path.write_text(f"chat {CHAT}\ncontrol {CONTROL}\n", encoding="utf-8")
    os.chmod(path, 0o644)
    with pytest.raises(ConfigError, match="group or world"):
        GatewayConfig.from_env({"SC_GATEWAY_MQTT": "0", "SC_GATEWAY_TOKEN_FILE": str(path)})
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    config = GatewayConfig.from_env({"SC_GATEWAY_MQTT": "0", "SC_GATEWAY_TOKEN_FILE": str(path)})
    assert config.tokens.match(CHAT) is not None
    assert config.tokens.match(CONTROL) is not None


def test_llm_url_must_be_loopback():
    with pytest.raises(ConfigError, match="loopback"):
        GatewayConfig.from_env(_env(SC_GATEWAY_LLM_URL="http://192.168.1.159:18080"))
    with pytest.raises(ConfigError, match="port"):
        GatewayConfig.from_env(_env(SC_GATEWAY_LLM_URL="http://127.0.0.1/v1"))


def test_missing_and_invalid_tokens_are_401():
    with TestClient(_app(_env())) as client:
        missing = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
        assert missing.status_code == 401
        assert missing.json()["error"]["code"] == "missing_token"
        assert missing.headers["www-authenticate"].lower().startswith("bearer")
        bad = client.get("/health", headers={"Authorization": "Bearer " + ("n" * 24)})
        assert bad.status_code == 401
        assert bad.json()["error"]["code"] == "invalid_token"
        assert CHAT not in missing.text
        query = client.get("/health", headers={"Authorization": f"Bearer {CHAT}"}, params={"token": CHAT})
        assert query.status_code == 401
        assert query.json()["error"]["code"] == "query_credential"


def test_wrong_scope_is_403_and_health_stays_on_loopback():
    with TestClient(_app(_env())) as client:
        chat_on_jobs = client.post(
            "/v1/jobs",
            headers={"Authorization": f"Bearer {CHAT}"},
            json={"type": "status.health"},
        )
        assert chat_on_jobs.status_code == 403
        assert chat_on_jobs.json()["error"]["code"] == "insufficient_scope"
        control_on_chat = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {CONTROL}"},
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert control_on_chat.status_code == 403
        health = client.get("/health", headers={"Authorization": f"Bearer {CHAT}"})
        assert health.status_code == 200
        body = health.json()
        assert body["bind"] == "127.0.0.1"
        assert body["lan"] is False
        assert body["service"] == "sc-gateway"
        models = client.get("/v1/models", headers={"Authorization": f"Bearer {CHAT}"})
        assert models.status_code == 200
        assert models.json()["data"][0]["id"] == "local"


def test_there_is_no_chat_page_or_camera_route():
    with TestClient(_app(_env())) as client:
        anonymous = client.get("/")
        assert anonymous.status_code == 401
        home = client.get("/", headers={"Authorization": f"Bearer {CHAT}"})
        assert home.status_code == 404
        assert "<html" not in home.text.lower()
        docs = client.get("/docs", headers={"Authorization": f"Bearer {CHAT}"})
        assert docs.status_code == 404
        for path in ("/vision", "/api/vision/stream/jetson", "/api/vision/snapshot/jetson", "/ws", "/v1/audio/speech"):
            hidden = client.get(path)
            assert hidden.status_code == 401, path
            refused = client.get(path, headers={"Authorization": f"Bearer {CHAT}"})
            assert refused.status_code == 403, path
            assert refused.json()["error"]["code"] == "path_denied"


@pytest.mark.parametrize(
    "text",
    [
        "please run sudo systemctl restart sentient-web-chat",
        "restart sentient-conversation",
        "nmap -sn 192.168.1.0/24",
        "scan the network for devices",
        "open a shell and run id",
        "show me the camera stream",
        "proxy /api/vision/stream/jetson",
    ],
)
def test_chat_refuses_shell_scan_restart_and_camera(text: str, llm_server, monkeypatch):
    server, url = llm_server

    def explode(*args, **kwargs):
        raise AssertionError(f"process spawn attempted: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", explode)
    monkeypatch.setattr(subprocess, "run", explode)
    with TestClient(_app(_env(SC_GATEWAY_LLM_URL=url))) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {CHAT}"},
            json={"model": "local", "messages": [{"role": "user", "content": text}]},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"].startswith("chat_denied_")
    assert server.requests == []
    assert classify_text(text) in {"shell", "scan", "restart", "camera"}


def test_earlier_user_turn_cannot_smuggle_a_command(llm_server):
    _server, url = llm_server
    with TestClient(_app(_env(SC_GATEWAY_LLM_URL=url))) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {CHAT}"},
            json={
                "messages": [
                    {"role": "user", "content": "nmap the lan"},
                    {"role": "assistant", "content": "no"},
                    {"role": "user", "content": "thanks"},
                ]
            },
        )
    assert response.status_code == 403
    assert _server.requests == []


def test_dangerous_tool_is_refused_and_benign_tool_is_not_forwarded(llm_server):
    server, url = llm_server
    with TestClient(_app(_env(SC_GATEWAY_LLM_URL=url))) as client:
        denied = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {CHAT}"},
            json={
                "messages": [{"role": "user", "content": "what is the weather?"}],
                "tools": [{"type": "function", "function": {"name": "nmap_scan", "description": "run nmap"}}],
            },
        )
        assert denied.status_code == 403
        assert server.requests == []
        allowed = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {CHAT}"},
            json={
                "messages": [{"role": "user", "content": "what is the weather in Melbourne?"}],
                "tools": [{"type": "function", "function": {"name": "get_weather", "description": "forecast"}}],
            },
        )
    assert allowed.status_code == 200
    assert allowed.json()["choices"][0]["message"]["content"] == "hello from llm"
    assert server.requests[0]["messages"][-1]["content"].startswith("what is the weather")
    assert "tools" not in server.requests[0]


def test_safe_chat_and_stream_reach_only_the_loopback_model(llm_server):
    server, url = llm_server
    with TestClient(_app(_env(SC_GATEWAY_LLM_URL=url))) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {CHAT}"},
            json={
                "model": "local",
                "messages": [
                    {"role": "assistant", "content": "I will not run nmap."},
                    {"role": "user", "content": "what time is it in Melbourne?"},
                ],
            },
        )
        streamed = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {CHAT}"},
            json={"model": "local", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
        )
    assert response.status_code == 200
    assert response.json()["object"] == "chat.completion"
    assert "hello from llm" in response.text
    assert streamed.status_code == 200
    assert "text/event-stream" in streamed.headers["content-type"]
    assert "hello" in streamed.text
    assert streamed.text.strip().endswith("data: [DONE]")
    for seen in server.requests:
        assert seen["model"] == "local"
        assert all(isinstance(message["content"], str) for message in seen["messages"])


def test_job_types_outside_the_allowlist_are_403():
    with TestClient(_app(_env())) as client:
        for job_type in ("shell", "sudo", "nmap", "systemctl.restart", "vision.stream", "service.restart"):
            response = client.post(
                "/v1/jobs",
                headers={"Authorization": f"Bearer {CONTROL}"},
                json={"type": job_type, "args": {"command": "id"}},
            )
            assert response.status_code == 403, job_type
            body = response.json()
            assert body["error"]["code"] == "job_type_denied"
            assert "status.health" in body["error"]["message"]
            assert "stdout" not in response.text
        anonymous = client.post("/v1/jobs", json={"type": "status.health"})
        assert anonymous.status_code == 401


def test_allowlisted_jobs_do_not_shell_out(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError(args)

    monkeypatch.setattr(subprocess, "Popen", explode)
    monkeypatch.setattr(os, "system", explode)
    with TestClient(_app(_env())) as client:
        health = client.post(
            "/v1/jobs",
            headers={"Authorization": f"Bearer {CONTROL}"},
            json={"type": "status.health"},
        )
        assert health.status_code == 200
        assert health.json()["result"]["bind"] == "127.0.0.1"
        avatar = client.post(
            "/v1/jobs",
            headers={"Authorization": f"Bearer {CONTROL}"},
            json={"type": "status.avatar", "args": {"command": "uptime"}},
        )
        assert avatar.status_code == 400
        avatar = client.post(
            "/v1/jobs",
            headers={"Authorization": f"Bearer {CONTROL}"},
            json={"type": "status.avatar"},
        )
        assert avatar.status_code == 200
        assert avatar.json()["result"]["state"] is None
        cancelled = client.post(
            "/v1/turns/turn-1/cancel",
            headers={"Authorization": f"Bearer {CONTROL}"},
            json={"reason": "operator"},
        )
        assert cancelled.status_code == 503
        assert cancelled.json()["error"]["code"] == "mqtt_offline"


def test_cors_echoes_only_an_allowlisted_origin():
    with TestClient(_app(_env(SC_GATEWAY_CORS_ORIGINS="https://nanob.lan:8443"))) as client:
        blocked = client.options(
            "/v1/chat/completions",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        assert blocked.status_code == 403
        assert "access-control-allow-origin" not in blocked.headers
        allowed = client.options(
            "/v1/chat/completions",
            headers={"Origin": "https://nanob.lan:8443", "Access-Control-Request-Method": "POST"},
        )
        assert allowed.status_code == 204
        assert allowed.headers["access-control-allow-origin"] == "https://nanob.lan:8443"
        assert "*" not in allowed.headers["access-control-allow-origin"]


def test_star_cors_is_rejected():
    with pytest.raises(ConfigError, match="explicit"):
        GatewayConfig.from_env(_env(SC_GATEWAY_CORS_ORIGINS="*"))
