"""MQTT session for sc-gateway.

Publishes chat/input and turn/cancel. Subscribes to the design's gateway
topics and keeps the latest retained avatar state. A disconnect fails any
turn that is still waiting so the HTTP handler can return 503.
"""

from __future__ import annotations

import asyncio
import json
import ssl
from dataclasses import dataclass, field
from typing import Any

from sentient.common.logging import setup_logging
from sentient.services.gateway.config import MqttSettings
from sentient.services.gateway.errors import GatewayError
from sentient.services.gateway.topics import (
    AVATAR_STATE,
    CHAT_INPUT,
    CHAT_OUTPUT,
    CHAT_STREAM,
    HEALTH,
    SOURCE,
    SUBSCRIPTIONS,
    TURN_CANCEL,
    topic,
)

log = setup_logging("gateway")


def now_iso() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Australia/Melbourne")).isoformat(timespec="milliseconds")


@dataclass
class _Turn:
    future: asyncio.Future[str]
    parts: list[str] = field(default_factory=list)
    text: str | None = None
    finished: bool = False


class MqttBridge:
    def __init__(self, settings: MqttSettings) -> None:
        self._settings = settings
        self._client: Any = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.connected = False
        self._turns: dict[str, _Turn] = {}
        self._streams: dict[str, asyncio.Queue[str | None]] = {}
        self._avatar: dict[str, Any] | None = None
        self._wake: dict[str, Any] | None = None
        self._tts: dict[str, Any] | None = None

    async def start(self) -> None:
        if not self._settings.enabled:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="sc-gateway-mqtt")

    async def stop(self) -> None:
        self._stop.set()
        await self._publish_health("offline")
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.connected = False
        self._fail_waiters(GatewayError(503, "mqtt_offline", "MQTT broker is not connected.", "server_error"))

    async def publish_chat(self, payload: dict[str, Any]) -> None:
        await self._publish(CHAT_INPUT, payload, qos=1, retain=False)

    async def publish_cancel(self, turn_id: str, reason: str) -> None:
        await self._publish(
            TURN_CANCEL,
            {
                "turn_id": turn_id,
                "ts": now_iso(),
                "source": SOURCE,
                "reason": reason,
            },
            qos=1,
            retain=False,
        )

    def arm(self, turn_id: str) -> None:
        loop = asyncio.get_running_loop()
        self._turns[turn_id] = _Turn(future=loop.create_future())
        self._streams[turn_id] = asyncio.Queue()

    def discard(self, turn_id: str) -> None:
        state = self._turns.pop(turn_id, None)
        self._streams.pop(turn_id, None)
        if state is not None and not state.future.done():
            state.future.cancel()

    async def wait_text(self, turn_id: str, timeout: float) -> str:
        state = self._turns.get(turn_id)
        if state is None:
            raise GatewayError(500, "turn_missing", "Turn was not armed.", "server_error")
        try:
            if state.finished and state.text is not None:
                return state.text
            return await asyncio.wait_for(state.future, timeout)
        except asyncio.TimeoutError as exc:
            raise GatewayError(
                504,
                "chat_timeout",
                "The brain did not answer before the chat deadline.",
                "server_error",
            ) from exc
        finally:
            self._turns.pop(turn_id, None)
            self._streams.pop(turn_id, None)

    async def iter_deltas(self, turn_id: str, timeout: float):
        queue = self._streams.get(turn_id)
        if queue is None:
            raise GatewayError(500, "turn_missing", "Turn was not armed.", "server_error")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise GatewayError(
                        504,
                        "chat_timeout",
                        "The brain did not answer before the chat deadline.",
                        "server_error",
                    )
                try:
                    item = await asyncio.wait_for(queue.get(), remaining)
                except asyncio.TimeoutError as exc:
                    raise GatewayError(
                        504,
                        "chat_timeout",
                        "The brain did not answer before the chat deadline.",
                        "server_error",
                    ) from exc
                if item is None:
                    return
                yield item
        finally:
            self._turns.pop(turn_id, None)
            self._streams.pop(turn_id, None)

    def avatar_state(self) -> dict[str, Any] | None:
        return self._avatar

    def status(self) -> dict[str, Any]:
        return {
            "mqtt_connected": self.connected,
            "avatar": self._avatar,
            "last_wake": self._wake,
            "last_tts": self._tts,
        }

    async def _run(self) -> None:
        try:
            import aiomqtt
        except ImportError as exc:
            log.error("aiomqtt is not installed; MQTT stays disconnected")
            self._fail_waiters(GatewayError(503, "mqtt_offline", "MQTT client library is missing.", "server_error"))
            raise RuntimeError("aiomqtt is required when MQTT is enabled") from exc

        delay = 1
        while not self._stop.is_set():
            try:
                client_kwargs: dict[str, Any] = {
                    "hostname": self._settings.host,
                    "port": self._settings.port,
                    "username": self._settings.username,
                    "password": self._settings.password,
                    "identifier": "sc-gateway",
                    "timeout": 10,
                    "will": aiomqtt.Will(
                        HEALTH,
                        json.dumps(_health("offline")),
                        qos=1,
                        retain=True,
                    ),
                }
                if self._settings.tls_ca:
                    context = ssl.create_default_context(cafile=self._settings.tls_ca)
                    context.minimum_version = ssl.TLSVersion.TLSv1_2
                    client_kwargs["tls_context"] = context
                async with aiomqtt.Client(**client_kwargs) as client:
                    self._client = client
                    delay = 1
                    log.info("MQTT connected to %s:%s", self._settings.host, self._settings.port)
                    await client.publish(HEALTH, json.dumps(_health("online")), qos=1, retain=True)
                    for subscribed, qos in SUBSCRIPTIONS:
                        await client.subscribe(subscribed, qos=qos)
                    self.connected = True
                    async for message in client.messages:
                        if self._stop.is_set():
                            break
                        self._on_message(str(message.topic), message.payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self._client = None
                if self._stop.is_set():
                    break
                log.warning("MQTT disconnected (%s); retrying in %ss", type(exc).__name__, delay)
                self._fail_waiters(
                    GatewayError(503, "mqtt_offline", "MQTT broker is not connected.", "server_error")
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * 2, 5)
            finally:
                self.connected = False
                self._client = None

    def _on_message(self, topic_name: str, payload: bytes | str) -> None:
        if isinstance(payload, bytes):
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                log.info("ignored non-utf8 MQTT payload on %s", topic_name)
                return
        else:
            text = payload
        if len(text) > 65536:
            log.info("ignored oversized MQTT payload on %s", topic_name)
            return
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            log.info("ignored non-json MQTT payload on %s", topic_name)
            return
        if not isinstance(body, dict):
            return
        if topic_name == AVATAR_STATE:
            self._avatar = body
            return
        if topic_name == topic("voice/wake"):
            self._wake = body
            return
        if topic_name == topic("tts/state"):
            self._tts = body
            return
        turn_id = body.get("turn_id")
        if not isinstance(turn_id, str):
            return
        state = self._turns.get(turn_id)
        if state is None:
            return
        if topic_name == CHAT_STREAM:
            delta = body.get("delta")
            if isinstance(delta, str) and delta:
                state.parts.append(delta)
                queue = self._streams.get(turn_id)
                if queue is not None:
                    queue.put_nowait(delta)
            if body.get("done") is True:
                self._finish(state, turn_id, "".join(state.parts))
            return
        if topic_name == CHAT_OUTPUT:
            output = body.get("text")
            if not isinstance(output, str):
                output = body.get("content") if isinstance(body.get("content"), str) else ""
            self._finish(state, turn_id, output)

    def _finish(self, state: _Turn, turn_id: str, text: str) -> None:
        state.text = text
        state.finished = True
        if not state.future.done():
            state.future.set_result(text)
        queue = self._streams.get(turn_id)
        if queue is not None:
            queue.put_nowait(None)

    async def _publish(self, topic_name: str, payload: dict[str, Any], qos: int, retain: bool) -> None:
        client = self._client
        if client is None or not self.connected:
            raise GatewayError(503, "mqtt_offline", "MQTT broker is not connected.", "server_error")
        try:
            await client.publish(topic_name, json.dumps(payload), qos=qos, retain=retain)
        except Exception as exc:
            log.warning("MQTT publish to %s failed (%s)", topic_name, type(exc).__name__)
            raise GatewayError(503, "mqtt_publish_failed", "MQTT publish failed.", "server_error") from exc

    async def _publish_health(self, status: str) -> None:
        client = self._client
        if client is None:
            return
        try:
            await client.publish(HEALTH, json.dumps(_health(status)), qos=1, retain=True)
        except Exception:
            log.info("could not publish offline health before disconnect")

    def _fail_waiters(self, error: GatewayError) -> None:
        for state in list(self._turns.values()):
            if state.future.done():
                continue
            state.future.set_exception(
                GatewayError(error.status, error.code, error.message, error.error_type)
            )
        for queue in list(self._streams.values()):
            queue.put_nowait(None)
        self._turns.clear()
        self._streams.clear()


def _health(status: str) -> dict[str, str]:
    return {"ts": now_iso(), "source": SOURCE, "service": "gateway", "status": status}
