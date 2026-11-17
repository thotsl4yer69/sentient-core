"""sc-brain conversation service.

Owns a turn from input through cancellation or a deadline, and speaks to the
LLM only via the contemplation adapter (``chat_stream`` / ``generate`` / ``close``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from typing import Any, Optional

from aiomqtt import Client, Will

from sentient.common import mqtt_topics
from sentient.common.mqtt_topics import orin_health_topic
from sentient.services.conversation.contract import (
    assert_constants_match,
    format_ts,
    load_contract,
    validate_payload,
)
from sentient.services.conversation.turns import TurnEngine, TurnLimits

logger = logging.getLogger("sentient.sc-brain")

SERVICE = "sc-brain"


class ConversationBrain:
    """MQTT front for :class:`TurnEngine` on the sc/v1 contract."""

    def __init__(
        self,
        llm: Any,
        *,
        host: str = "127.0.0.1",
        port: int = 1883,
        username: Optional[str] = None,
        password: Optional[str] = None,
        model: str = "local",
        limits: Optional[TurnLimits] = None,
        contract: Optional[dict[str, Any]] = None,
    ):
        self.llm = llm
        self.host = host
        self.port = port
        self.username = username or None
        self.password = password or None
        self.model = model
        self.service = SERVICE
        self.contract = contract or load_contract()
        assert_constants_match(self.contract, mqtt_topics)
        self._by_topic = {topic["topic"]: topic for topic in self.contract["topics"]}
        self._client: Optional[Client] = None
        self._stopped = False
        self.engine = TurnEngine(
            llm,
            self.publish,
            limits=limits,
            model=model,
            source=self.service,
        )

    def subscriptions(self) -> list[dict[str, Any]]:
        """Topics the contract says sc-brain subscribes to."""
        return [topic for topic in self.contract["topics"] if self.service in topic["subscribers"]]

    async def publish(self, topic_id: str, payload: dict[str, Any]) -> None:
        errors = validate_payload(topic_id, payload, self.contract)
        if errors:
            logger.error("refusing invalid %s payload: %s", topic_id, errors)
            return
        client = self._client
        if client is None:
            logger.warning("mqtt not connected; dropping %s", topic_id)
            return
        await client.publish(
            mqtt_topics.ORIN_TOPICS[topic_id],
            json.dumps(payload).encode(),
            qos=int(mqtt_topics.ORIN_QOS[topic_id]),
            retain=bool(mqtt_topics.ORIN_RETAIN[topic_id]),
        )

    async def serve(self, ready: Optional[asyncio.Event] = None) -> None:
        """Run until cancelled. Reconnects if the broker drops the session."""
        await self.engine.start()
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
            await self.engine.stop()
            close = getattr(self.llm, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    logger.exception("llm close failed")

    async def stop(self) -> None:
        self._stopped = True
        client = self._client
        if client is not None:
            try:
                await self._publish_health(client, "down")
            except Exception:
                logger.debug("health down failed", exc_info=True)

    async def _session(self, ready: Optional[asyncio.Event]) -> None:
        will_payload = json.dumps(self._health_body("down")).encode()
        async with Client(
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            identifier=self.service,
            will=Will(orin_health_topic(self.service), will_payload, qos=1, retain=True),
        ) as client:
            self._client = client
            try:
                for topic in self.subscriptions():
                    await client.subscribe(topic["topic"], qos=int(topic["qos"]))
                    logger.info("Subscribed to %s", topic["topic"])
                await self._publish_health(client, "healthy")
                if ready is not None and not ready.is_set():
                    ready.set()
                async for message in client.messages:
                    if self._stopped:
                        break
                    try:
                        await self._on_message(message)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("handler failed for %s", message.topic)
            finally:
                try:
                    await self._publish_health(client, "down")
                except Exception:
                    logger.debug("session health down failed", exc_info=True)
                if self._client is client:
                    self._client = None

    async def _on_message(self, message: Any) -> None:
        topic_name = str(message.topic)
        spec = self._by_topic.get(topic_name)
        if spec is None:
            return
        try:
            payload = json.loads(bytes(message.payload).decode())
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            logger.warning("ignoring non-json payload on %s", topic_name)
            return
        if not isinstance(payload, dict):
            logger.warning("ignoring non-object payload on %s", topic_name)
            return
        errors = validate_payload(spec["id"], payload, self.contract)
        if errors:
            logger.warning("ignoring invalid %s: %s", spec["id"], errors)
            return
        if spec["id"] == "voice/wake":
            await self.engine.handle_wake(payload)
        elif spec["id"] == "voice/transcript":
            await self.engine.handle_transcript(payload)
        elif spec["id"] == "chat/input":
            await self.engine.handle_chat_input(payload)
        elif spec["id"] == "turn/cancel":
            await self.engine.handle_cancel(payload)
        elif spec["id"] == "tts/state":
            await self.engine.handle_tts_state(payload)

    def _health_body(self, status: str) -> dict[str, str]:
        return {
            "ts": format_ts(),
            "source": self.service,
            "status": status,
            "service": self.service,
        }

    async def _publish_health(self, client: Client, status: str) -> None:
        payload = self._health_body(status)
        errors = validate_payload("health/{service}", payload, self.contract)
        if errors:
            raise RuntimeError(f"health payload invalid: {errors}")
        await client.publish(
            orin_health_topic(self.service),
            json.dumps(payload).encode(),
            qos=int(mqtt_topics.ORIN_QOS["health/{service}"]),
            retain=bool(mqtt_topics.ORIN_RETAIN["health/{service}"]),
        )


def brain_from_env(llm: Any) -> ConversationBrain:
    username = os.environ.get("SC_MQTT_USERNAME") or None
    password = os.environ.get("SC_MQTT_PASSWORD") or None
    return ConversationBrain(
        llm,
        host=os.environ.get("SC_MQTT_HOST", "127.0.0.1"),
        port=int(os.environ.get("SC_MQTT_PORT", "1883")),
        username=username,
        password=password,
        model=os.environ.get("SC_LLM_MODEL", "local"),
    )


def main() -> None:
    """Run sc-brain against the local contemplation adapter."""
    from sentient.services.contemplation.local_openai import LocalOpenAIClient

    llm = LocalOpenAIClient(os.environ.get("SC_LLM_HOST", "http://127.0.0.1:18080"))
    brain = brain_from_env(llm)

    async def _run() -> None:
        task = asyncio.create_task(brain.serve())
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
        await brain.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return

    asyncio.run(_run())


if __name__ == "__main__":
    main()
