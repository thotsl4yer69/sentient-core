"""MQTT auth and ACL checks against a real Mosquitto broker.

Mosquitto 2.0's built-in acl_file plugin accepts every SUBSCRIBE. These tests
load the dynamic-security database built from deploy/mosquitto/acl, which is
what makes a denied MQTT 3.1.1 subscribe return SUBACK 0x80 and a denied
MQTT 5 publish return PUBACK 0x87.
"""

from __future__ import annotations

import json
import shutil
import ssl
import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from paho.mqtt.client import Client
from paho.mqtt.enums import CallbackAPIVersion, MQTTProtocolVersion

from sentient.services.gateway.app import create_app
from sentient.services.gateway.config import GatewayConfig
from tests.security.harness import REPO, apply_acl, listening_addresses, start_broker

CONF = REPO / "deploy" / "mosquitto" / "mosquitto.conf"
ACL = REPO / "deploy" / "mosquitto" / "acl"

EXPECTED = {
    "sc-gateway": [
        ("read", "sc/v1/voice/wake"),
        ("read", "sc/v1/chat/stream"),
        ("read", "sc/v1/chat/output"),
        ("read", "sc/v1/tts/state"),
        ("read", "sc/v1/avatar/state"),
        ("write", "sc/v1/chat/input"),
        ("write", "sc/v1/turn/cancel"),
        ("write", "sc/v1/health/gateway"),
    ],
    "sc-brain": [
        ("read", "sc/v1/voice/wake"),
        ("read", "sc/v1/voice/transcript"),
        ("read", "sc/v1/chat/input"),
        ("read", "sc/v1/turn/cancel"),
        ("read", "sc/v1/tts/state"),
        ("write", "sc/v1/chat/stream"),
        ("write", "sc/v1/chat/output"),
        ("write", "sc/v1/tts/say"),
        ("write", "sc/v1/avatar/state"),
        ("write", "sc/v1/health/brain"),
    ],
    "sc-speech": [
        ("read", "sc/v1/tts/say"),
        ("read", "sc/v1/turn/cancel"),
        ("write", "sc/v1/voice/wake"),
        ("write", "sc/v1/voice/transcript"),
        ("write", "sc/v1/tts/state"),
        ("write", "sc/v1/health/speech"),
    ],
    "sc-buddy": [
        ("read", "sc/v1/chat/stream"),
        ("read", "sc/v1/chat/output"),
        ("write", "sc/v1/chat/input"),
        ("write", "sc/v1/health/buddy"),
    ],
    "sc-memory": [
        ("read", "sc/v1/chat/output"),
        ("write", "sc/v1/health/memory"),
    ],
    "sc-health": [
        ("read", "sc/v1/health/#"),
        ("write", "sc/v1/health/health"),
    ],
    "sc-compat": [
        ("readwrite", "sc/v1/#"),
        ("readwrite", "sentient/#"),
    ],
}


def _code(reason) -> int:
    return int(getattr(reason, "value", reason))


class Session:
    def __init__(self, port: int, username: str | None, password: str | None, protocol, tls_ca: Path | None = None):
        self.messages: list[tuple[str, bytes]] = []
        self.pubacks: list[int] = []
        self.subacks: list[list[int]] = []
        self.conn: int | None = None
        self._conn_event = threading.Event()
        self._lock = threading.Lock()
        client_id = "probe-" + uuid.uuid4().hex[:12]
        self.client = Client(
            CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=protocol,
            reconnect_on_failure=False,
        )
        if tls_ca is not None:
            self.client.tls_set(ca_certs=str(tls_ca), cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        if username is not None:
            self.client.username_pw_set(username, password)

        def on_connect(_client, _userdata, _flags, reason_code, _properties):
            self.conn = _code(reason_code)
            self._conn_event.set()
            if self.conn != 0:
                _client.disconnect()

        def on_publish(_client, _userdata, _mid, reason_code, _properties):
            with self._lock:
                self.pubacks.append(_code(reason_code))

        def on_subscribe(_client, _userdata, _mid, reason_codes, _properties):
            with self._lock:
                self.subacks.append([_code(item) for item in reason_codes])

        def on_message(_client, _userdata, message):
            with self._lock:
                self.messages.append((message.topic, bytes(message.payload)))

        self.client.on_connect = on_connect
        self.client.on_publish = on_publish
        self.client.on_subscribe = on_subscribe
        self.client.on_message = on_message
        self._port = port

    def start(self) -> None:
        self.client.connect("127.0.0.1", self._port, keepalive=10)
        self.client.loop_start()

    def wait_conn(self, timeout: float = 3) -> int | None:
        self._conn_event.wait(timeout)
        return self.conn

    def subscribe(self, topic: str, qos: int = 1) -> list[int]:
        before = len(self.subacks)
        self.client.subscribe(topic, qos=qos)
        found = self._wait(self.subacks, before)
        return found[-1]

    def publish(self, topic: str, payload: bytes, qos: int = 1) -> int:
        before = len(self.pubacks)
        self.client.publish(topic, payload, qos=qos)
        found = self._wait(self.pubacks, before)
        return found[-1]

    def wait_messages(self, count: int, timeout: float = 2) -> list[tuple[str, bytes]]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if len(self.messages) >= count:
                    return list(self.messages)
            time.sleep(0.05)
        with self._lock:
            return list(self.messages)

    def _wait(self, bucket: list, before: int, timeout: float = 3):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if len(bucket) > before:
                    return list(bucket)
            time.sleep(0.05)
        raise TimeoutError("MQTT acknowledgement did not arrive")

    def close(self) -> None:
        self.client.loop_stop()
        try:
            self.client.disconnect()
        except Exception:
            return


@pytest.fixture(scope="module")
def broker():
    handle = start_broker()
    try:
        yield handle
    finally:
        handle.stop()
        shutil.rmtree(handle.dest, ignore_errors=True)


def test_shipped_broker_config_keeps_plaintext_on_loopback_and_uses_tls():
    text = CONF.read_text(encoding="utf-8")
    assert "allow_anonymous false" in text
    assert "listener 1883 127.0.0.1" in text
    assert "listener 8883\n" in text
    assert "tls_version tlsv1.2" in text
    assert "cafile /etc/mosquitto/sc/certs/ca.crt" in text
    assert "certfile /etc/mosquitto/sc/certs/server.crt" in text
    assert "keyfile /etc/mosquitto/sc/certs/server.key" in text
    assert "require_certificate false" in text
    assert "include_dir /etc/mosquitto/sc/conf.d" in text
    assert "plugin /usr/lib" not in text
    for line in text.splitlines():
        stripped = line.strip()
        assert stripped not in {"listener 1883", "listener 1883 0.0.0.0"}
        assert not stripped.startswith("acl_file")
        assert not stripped.startswith("password_file")
        assert stripped != "allow_anonymous true"
    assert apply_acl.parse_acl(ACL) == EXPECTED


def test_bootstrap_refuses_to_write_secrets_into_the_repo():
    with pytest.raises(apply_acl.AclError):
        apply_acl.assert_dest_outside_repo(REPO)


def test_anonymous_and_wrong_password_are_refused_at_connack(broker):
    for protocol in (MQTTProtocolVersion.MQTTv5, MQTTProtocolVersion.MQTTv311):
        anonymous = Session(broker.plain_port, None, None, protocol)
        anonymous.start()
        assert anonymous.wait_conn() not in {None, 0}
        anonymous.close()
        wrong = Session(broker.plain_port, "sc-gateway", "not-the-password", protocol)
        wrong.start()
        assert wrong.wait_conn() not in {None, 0}
        wrong.close()


def test_disabled_admin_cannot_connect(broker):
    database = json.loads((broker.dest / "dynamic-security.json").read_text(encoding="utf-8"))
    clients = {item["username"]: item for item in database["clients"]}
    assert clients["admin"].get("disabled") is True
    assert database["defaultACLAccess"] == {
        "publishClientSend": False,
        "publishClientReceive": False,
        "subscribe": False,
        "unsubscribe": False,
    }
    admin = Session(broker.plain_port, "admin", "whatever-admin-password", MQTTProtocolVersion.MQTTv5)
    admin.start()
    assert admin.wait_conn() not in {None, 0}
    admin.close()
    mode = (broker.dest / "dynamic-security.json").stat().st_mode & 0o077
    assert mode == 0


def test_denied_subscribe_is_suback_0x80_and_denied_publish_is_puback_0x87(broker):
    gateway = Session(
        broker.plain_port,
        "sc-gateway",
        broker.passwords["sc-gateway"],
        MQTTProtocolVersion.MQTTv311,
    )
    gateway.start()
    assert gateway.wait_conn() == 0
    denied_exact = gateway.subscribe("sc/v1/voice/transcript")
    denied_wild = gateway.subscribe("#")
    allowed = gateway.subscribe("sc/v1/chat/output")
    assert denied_exact == [0x80]
    assert denied_wild == [0x80]
    assert allowed == [1]
    gateway.close()

    speaker = Session(
        broker.plain_port,
        "sc-gateway",
        broker.passwords["sc-gateway"],
        MQTTProtocolVersion.MQTTv5,
    )
    brain = Session(
        broker.plain_port,
        "sc-brain",
        broker.passwords["sc-brain"],
        MQTTProtocolVersion.MQTTv5,
    )
    speech = Session(
        broker.plain_port,
        "sc-speech",
        broker.passwords["sc-speech"],
        MQTTProtocolVersion.MQTTv5,
    )
    for client in (speaker, brain, speech):
        client.start()
        assert client.wait_conn() == 0
    assert brain.subscribe("sc/v1/chat/input") == [1]
    assert speech.subscribe("sc/v1/tts/say") == [1]
    assert speech.subscribe("sc/v1/chat/input") == [0x87]
    allowed_pub = speaker.publish("sc/v1/chat/input", b'{"ok":"input"}')
    denied_pub = speaker.publish("sc/v1/tts/say", b'{"no":"say"}')
    assert allowed_pub == 0
    assert denied_pub == 0x87
    brain_messages = brain.wait_messages(1)
    assert brain_messages == [("sc/v1/chat/input", b'{"ok":"input"}')]
    assert speech.wait_messages(1, timeout=0.4) == []
    for client in (speaker, brain, speech):
        client.close()


def test_other_clients_cannot_publish_outside_their_grant(broker):
    observer = Session(
        broker.plain_port,
        "sc-compat",
        broker.passwords["sc-compat"],
        MQTTProtocolVersion.MQTTv5,
    )
    brain = Session(
        broker.plain_port,
        "sc-brain",
        broker.passwords["sc-brain"],
        MQTTProtocolVersion.MQTTv5,
    )
    memory = Session(
        broker.plain_port,
        "sc-memory",
        broker.passwords["sc-memory"],
        MQTTProtocolVersion.MQTTv5,
    )
    for client in (observer, brain, memory):
        client.start()
        assert client.wait_conn() == 0
    assert observer.subscribe("sc/v1/chat/input") == [1]
    assert observer.subscribe("$SYS/#") == [0x87]
    assert brain.publish("sc/v1/chat/input", b'{"from":"brain"}') == 0x87
    assert memory.publish("sc/v1/chat/output", b'{"from":"memory"}') == 0x87
    assert observer.wait_messages(1, timeout=0.4) == []
    assert observer.subscribe("sentient/#") == [1]
    assert observer.publish("sentient/chat/input", b'{"legacy":1}') == 0
    legacy = observer.wait_messages(1)
    assert legacy == [("sentient/chat/input", b'{"legacy":1}')]
    for client in (observer, brain, memory):
        client.close()


def test_health_tree_is_readable_only_by_sc_health(broker):
    health = Session(
        broker.plain_port,
        "sc-health",
        broker.passwords["sc-health"],
        MQTTProtocolVersion.MQTTv5,
    )
    speech = Session(
        broker.plain_port,
        "sc-speech",
        broker.passwords["sc-speech"],
        MQTTProtocolVersion.MQTTv5,
    )
    gateway = Session(
        broker.plain_port,
        "sc-gateway",
        broker.passwords["sc-gateway"],
        MQTTProtocolVersion.MQTTv311,
    )
    for client in (health, speech, gateway):
        client.start()
        assert client.wait_conn() == 0
    assert health.subscribe("sc/v1/health/#") == [1]
    assert gateway.subscribe("sc/v1/health/#") == [0x80]
    assert speech.publish("sc/v1/health/speech", b'{"status":"online"}') == 0
    assert health.wait_messages(1) == [("sc/v1/health/speech", b'{"status":"online"}')]
    for client in (health, speech, gateway):
        client.close()


def test_plaintext_listener_is_loopback_and_tls_requires_a_password(broker):
    assert "127.0.0.1" in listening_addresses(broker.plain_port)
    assert "0.0.0.0" not in listening_addresses(broker.plain_port)
    assert "127.0.0.1" in listening_addresses(broker.tls_port)
    plaintext = Session(broker.tls_port, "sc-gateway", broker.passwords["sc-gateway"], MQTTProtocolVersion.MQTTv5)
    try:
        plaintext.start()
    except Exception:
        plaintext.conn = -1
    assert plaintext.wait_conn() in {None, -1} or (plaintext.conn not in {None, 0})
    plaintext.close()
    wrong = Session(
        broker.tls_port,
        "sc-gateway",
        "still-wrong",
        MQTTProtocolVersion.MQTTv5,
        tls_ca=broker.ca,
    )
    wrong.start()
    assert wrong.wait_conn() not in {None, 0}
    wrong.close()
    good = Session(
        broker.tls_port,
        "sc-gateway",
        broker.passwords["sc-gateway"],
        MQTTProtocolVersion.MQTTv5,
        tls_ca=broker.ca,
    )
    good.start()
    assert good.wait_conn() == 0
    assert good.subscribe("sc/v1/chat/output") == [1]
    good.close()


def test_gateway_chat_uses_the_broker_and_a_refused_chat_is_not_published(broker):
    seen: list[dict] = []
    lock = threading.Lock()
    brain = Session(
        broker.plain_port,
        "sc-brain",
        broker.passwords["sc-brain"],
        MQTTProtocolVersion.MQTTv5,
    )
    brain.start()
    assert brain.wait_conn() == 0

    def on_message(client, _userdata, message):
        if str(message.topic) != "sc/v1/chat/input":
            return
        payload = json.loads(message.payload)
        with lock:
            seen.append(payload)
        reply = {
            "turn_id": payload["turn_id"],
            "ts": payload["ts"],
            "source": "sc-brain",
            "text": "ack:" + payload["text"],
        }
        if payload.get("stream"):
            client.publish(
                "sc/v1/chat/stream",
                json.dumps(
                    {
                        "turn_id": payload["turn_id"],
                        "ts": payload["ts"],
                        "source": "sc-brain",
                        "delta": "ack:",
                        "done": False,
                    }
                ),
                qos=0,
            )
            client.publish(
                "sc/v1/chat/stream",
                json.dumps(
                    {
                        "turn_id": payload["turn_id"],
                        "ts": payload["ts"],
                        "source": "sc-brain",
                        "delta": payload["text"],
                        "done": True,
                    }
                ),
                qos=0,
            )
        client.publish("sc/v1/chat/output", json.dumps(reply), qos=1)

    brain.client.on_message = on_message
    assert brain.subscribe("sc/v1/chat/input") == [1]
    chat = "c" * 32
    control = "k" * 32
    config = GatewayConfig.from_env(
        {
            "SC_GATEWAY_TOKENS": f"chat:{chat},control:{control}",
            "SC_GATEWAY_MQTT": "1",
            "SC_MQTT_HOST": "127.0.0.1",
            "SC_MQTT_PORT": str(broker.plain_port),
            "SC_MQTT_USER": "sc-gateway",
            "SC_MQTT_PASS": broker.passwords["sc-gateway"],
            "SC_GATEWAY_CHAT_TIMEOUT": "5",
        }
    )
    with TestClient(create_app(config)) as client:
        deadline = time.time() + 5
        connected = False
        while time.time() < deadline:
            health = client.get("/health", headers={"Authorization": f"Bearer {chat}"})
            if health.status_code == 200 and health.json()["mqtt_connected"]:
                connected = True
                break
            time.sleep(0.1)
        assert connected
        refused = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {chat}"},
            json={"messages": [{"role": "user", "content": "sudo systemctl restart sentient-web-chat"}]},
        )
        assert refused.status_code == 403
        time.sleep(0.3)
        with lock:
            assert seen == []
        accepted = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {chat}"},
            json={"messages": [{"role": "user", "content": "what time is it in Melbourne?"}]},
        )
        assert accepted.status_code == 200
        assert accepted.json()["choices"][0]["message"]["content"] == "ack:what time is it in Melbourne?"
        streamed = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {chat}"},
            json={"stream": True, "messages": [{"role": "user", "content": "hello there"}]},
        )
        assert streamed.status_code == 200
        assert "ack:" in streamed.text and "hello there" in streamed.text
        cancel = client.post(
            "/v1/turns/turn-9/cancel",
            headers={"Authorization": f"Bearer {control}"},
            json={"reason": "operator"},
        )
        assert cancel.status_code == 200
        assert cancel.json()["result"]["cancelled"] is True
    brain.close()
