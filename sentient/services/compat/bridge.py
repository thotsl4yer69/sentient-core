"""sc-compat: bridge legacy ``sentient/`` topics and ``orin/v1`` while both stacks run.

AIRI and the avatar bridge keep publishing and subscribing to the legacy
topics, including ``sentient/persona/chat/input``, which the v2 orchestrator
never subscribed to. This process is the subscriber. A ``bridge`` field stops
a republished message from being bridged again.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import uuid
from typing import Any, Optional

from aiomqtt import Client, Will

from sentient.common.mqtt_topics import orin_health_topic
from sentient.services.conversation.contract import (
    format_ts,
    load_contract,
    normalize_ts,
    topic_by_name,
    validate_payload,
)

logger = logging.getLogger("sentient.sc-compat")

SERVICE = "sc-compat"
_CANCEL_REASONS = {"barge-in", "superseded", "deadline", "client"}
_LEGACY_CANCEL = {"wake_word_interrupt", "barge_in", "bargein"}


class CompatBridge:
    """Bidirectional legacy bridge driven entirely by contract/mqtt.yaml."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 1883,
        username: Optional[str] = None,
        password: Optional[str] = None,
        contract: Optional[dict[str, Any]] = None,
    ):
        self.host = host
        self.port = port
        self.username = username or None
        self.password = password or None
        self.contract = contract or load_contract()
        self.service = SERVICE
        self._by_orin = topic_by_name(self.contract)
        self._guard_field = self.contract["loop_guard"]["field"]
        self._guard_value = self.contract["loop_guard"]["value"]
        self._client: Optional[Client] = None
        self._stopped = False

    def subscriptions(self) -> list[tuple[str, int]]:
        """(topic, qos) pairs. QoS comes from the orin side of each bridge."""
        found: dict[str, int] = {}
        for bridge in self.contract["bridges"]:
            spec = self._by_orin[bridge["orin"]]
            qos = int(spec["qos"])
            names = _legacy_names(bridge)
            if bridge["direction"] in ("both", "to_orin"):
                for name in names:
                    found[name] = max(found.get(name, 0), 1)
            if bridge["direction"] in ("both", "to_legacy"):
                found[bridge["orin"]] = max(found.get(bridge["orin"], 0), qos)
        return sorted(found.items())

    def translate(self, topic: str, payload: dict[str, Any]) -> list[tuple[str, dict[str, Any], int, bool]]:
        """Map one message to the other side. Marked messages produce nothing."""
        if payload.get(self._guard_field) == self._guard_value:
            return []
        if topic in self._by_orin:
            return self._from_orin(topic, payload)
        bridge = self._bridge_for_legacy(topic)
        if bridge is None or bridge["direction"] not in ("both", "to_orin"):
            return []
        body = self._to_orin(topic, bridge, payload)
        if body is None:
            return []
        spec = self._by_orin[bridge["orin"]]
        errors = validate_payload(spec["id"], body, self.contract)
        if errors:
            logger.warning("dropping bridge %s -> %s: %s", topic, bridge["orin"], errors)
            return []
        return [(bridge["orin"], body, int(spec["qos"]), bool(spec["retain"]))]

    async def serve(self, ready: Optional[asyncio.Event] = None) -> None:
        delay = 1.0
        try:
            while not self._stopped:
                try:
                    await self._session(ready)
                    delay = 1.0
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("mqtt session ended; reconnecting in %.0fs", delay)
                    self._client = None
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30.0)
        finally:
            self._client = None

    async def stop(self) -> None:
        self._stopped = True

    async def _session(self, ready: Optional[asyncio.Event]) -> None:
        will = json.dumps(self._health_body("down")).encode()
        async with Client(
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            identifier=self.service,
            will=Will(orin_health_topic(self.service), will, qos=1, retain=True),
        ) as client:
            self._client = client
            try:
                for topic, qos in self.subscriptions():
                    await client.subscribe(topic, qos=qos)
                    logger.info("Subscribed to %s", topic)
                await client.publish(
                    orin_health_topic(self.service),
                    json.dumps(self._health_body("healthy")).encode(),
                    qos=1,
                    retain=True,
                )
                if ready is not None and not ready.is_set():
                    ready.set()
                async for message in client.messages:
                    if self._stopped:
                        break
                    await self._on_message(client, message)
            finally:
                try:
                    await client.publish(
                        orin_health_topic(self.service),
                        json.dumps(self._health_body("down")).encode(),
                        qos=1,
                        retain=True,
                    )
                except Exception:
                    logger.debug("health down failed", exc_info=True)
                if self._client is client:
                    self._client = None

    async def _on_message(self, client: Client, message: Any) -> None:
        topic = str(message.topic)
        try:
            payload = json.loads(bytes(message.payload).decode())
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            logger.warning("ignoring non-json payload on %s", topic)
            return
        if not isinstance(payload, dict):
            return
        for dest, body, qos, retain in self.translate(topic, payload):
            await client.publish(dest, json.dumps(body).encode(), qos=qos, retain=retain)
            logger.info("bridged %s -> %s turn %s", topic, dest, body.get("turn_id", "-"))

    def _from_orin(self, topic: str, payload: dict[str, Any]) -> list[tuple[str, dict[str, Any], int, bool]]:
        spec = self._by_orin[topic]
        outgoing: list[tuple[str, dict[str, Any], int, bool]] = []
        seen: set[str] = set()
        for bridge in self.contract["bridges"]:
            if bridge["orin"] != topic or bridge["direction"] not in ("both", "to_legacy"):
                continue
            names = [bridge["legacy"]]
            names.extend(_fanout(bridge, payload))
            for dest in names:
                if dest in seen:
                    continue
                seen.add(dest)
                outgoing.append((
                    dest,
                    self._to_legacy(dest, payload),
                    int(spec["qos"]),
                    bool(spec["retain"]),
                ))
        return outgoing

    def _bridge_for_legacy(self, topic: str) -> Optional[dict[str, Any]]:
        for bridge in self.contract["bridges"]:
            if topic in _legacy_names(bridge):
                return bridge
        return None

    def _to_orin(self, topic: str, bridge: dict[str, Any], payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        spec = self._by_orin[bridge["orin"]]
        data = dict(payload)
        if topic.endswith("/tts/started") and "state" not in data:
            data["state"] = "started"
        elif topic.endswith("/tts/completed") and "state" not in data:
            data["state"] = "completed"
        elif topic.endswith("/tts/stop") and "state" not in data:
            data.setdefault("reason", "barge-in")
        body: dict[str, Any] = {
            self._guard_field: self._guard_value,
            "ts": normalize_ts(data.get("ts", data.get("timestamp"))),
            "source": str(data.get("source") or "legacy"),
        }
        if spec["turn_id"] != "absent":
            turn_id = data.get("turn_id") or data.get("id") or data.get("conversation_id")
            if not turn_id:
                turn_id = "*" if spec["id"] == "turn/cancel" else str(uuid.uuid4())
            body["turn_id"] = str(turn_id)
        for name in spec["payload"]["properties"]:
            if name in body or name not in data:
                continue
            body[name] = data[name]
        if spec["id"] == "chat/input":
            text = data.get("text") or data.get("message") or data.get("content") or ""
            body["text"] = str(text)
            if not str(body["text"]).strip():
                return None
            if "kind" not in body:
                body["kind"] = "voice" if "voice" in body["source"] else "chat"
            if "user" not in body and data.get("user"):
                body["user"] = str(data["user"])
        if spec["id"] == "turn/cancel":
            body["reason"] = _cancel_reason(data)
        if spec["id"] == "voice/transcript":
            text = data.get("text") or data.get("message") or ""
            body["text"] = str(text)
            if not str(body["text"]).strip():
                return None
        if spec["id"] == "tts/say":
            body["text"] = str(data.get("text") or "")
            if not body["text"].strip():
                return None
        if spec["id"] == "avatar/state" and "state" not in body:
            body["state"] = str(data.get("expression") or data.get("emotion") or "idle")
        return body

    def _to_legacy(self, dest: str, payload: dict[str, Any]) -> dict[str, Any]:
        turn_id = payload.get("turn_id")
        body: dict[str, Any] = {
            self._guard_field: self._guard_value,
            "turn_id": turn_id,
            "ts": payload.get("ts") or format_ts(),
            "timestamp": payload.get("ts") or format_ts(),
            "source": payload.get("source") or SERVICE,
            "text": payload.get("text", ""),
            "user": payload.get("user", "jack"),
            "conversation_id": turn_id,
            "emotion": payload.get("emotion", "neutral"),
        }
        for name in ("state", "token", "partial", "done", "reason", "cancelled", "confidence", "attempt"):
            if name in payload:
                body[name] = payload[name]
        if dest.startswith("sentient/tts/"):
            body["id"] = turn_id
        if dest in ("sentient/avatar/expression", "sentient/persona/emotion"):
            body["expression"] = payload.get("emotion") or payload.get("state") or "neutral"
            body["intensity"] = payload.get("intensity", 0.7)
        return body

    def _health_body(self, status: str) -> dict[str, str]:
        return {
            "ts": format_ts(),
            "source": self.service,
            "status": status,
            "service": self.service,
        }


def _legacy_names(bridge: dict[str, Any]) -> list[str]:
    names = [bridge["legacy"]]
    names.extend(bridge.get("also_legacy") or [])
    names.extend(rule["topic"] for rule in bridge.get("fanout_topics") or [])
    return names


def _fanout(bridge: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    state = str(payload.get("state") or "")
    names: list[str] = []
    for rule in bridge.get("fanout_topics") or []:
        when = rule["when"]
        if when == "emotion" and payload.get("emotion"):
            names.append(rule["topic"])
        elif when == state:
            names.append(rule["topic"])
    names.extend(bridge.get("also_legacy") or [])
    return names


def _cancel_reason(payload: dict[str, Any]) -> str:
    raw = str(payload.get("reason") or "")
    if raw in _CANCEL_REASONS:
        return raw
    if raw in _LEGACY_CANCEL:
        return "barge-in"
    return "client"


def bridge_from_env() -> CompatBridge:
    return CompatBridge(
        host=os.environ.get("SC_MQTT_HOST", "127.0.0.1"),
        port=int(os.environ.get("SC_MQTT_PORT", "1883")),
        username=os.environ.get("SC_MQTT_USERNAME") or None,
        password=os.environ.get("SC_MQTT_PASSWORD") or None,
    )


def main() -> None:
    bridge = bridge_from_env()

    async def _run() -> None:
        task = asyncio.create_task(bridge.serve())
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
        await bridge.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return

    asyncio.run(_run())
