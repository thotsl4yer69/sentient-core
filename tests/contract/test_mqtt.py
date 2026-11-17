"""Contract behaviour against a real mosquitto broker."""

from __future__ import annotations

import asyncio
import unittest

from sentient.services.compat.bridge import CompatBridge
from sentient.services.conversation.brain import ConversationBrain
from sentient.services.conversation.contract import format_ts
from sentient.services.conversation.turns import TurnLimits
from tests.contract.broker import Collector, Mosquitto
from tests.contract.fakes import ScriptedLLM

ORIN_TOPICS = [
    ("orin/v1/chat/input", 1),
    ("orin/v1/chat/stream", 0),
    ("orin/v1/chat/output", 1),
    ("orin/v1/tts/say", 1),
    ("orin/v1/avatar/state", 0),
    ("orin/v1/voice/wake", 1),
    ("orin/v1/turn/cancel", 1),
    ("orin/v1/health/#", 1),
    ("sentient/persona/chat/input", 1),
    ("sentient/chat/input", 1),
    ("sentient/chat/output", 1),
    ("sentient/persona/chat/output", 1),
    ("sentient/persona/response", 1),
    ("sentient/tts/request", 1),
    ("sentient/tts/stop", 1),
]


def _on(messages, topic: str, **fields):
    found = []
    for seen, payload in messages:
        if seen != topic:
            continue
        if all(payload.get(key) == value for key, value in fields.items()):
            found.append(payload)
    return found


class BrainMqttTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_turn_round_trip(self):
        llm = ScriptedLLM(["Hello there"])
        async with Mosquitto() as broker, Collector(broker.port, ORIN_TOPICS) as collector:
            ready = asyncio.Event()
            brain = ConversationBrain(llm, port=broker.port)
            task = asyncio.create_task(brain.serve(ready))
            try:
                await asyncio.wait_for(ready.wait(), 3)
                await collector.wait_until(
                    lambda messages: bool(_on(messages, "orin/v1/health/sc-brain", status="healthy")),
                    timeout=2,
                )
                await collector.publish("orin/v1/chat/input", {
                    "turn_id": "wire-1",
                    "ts": format_ts(),
                    "source": "sc-gateway",
                    "text": "Hi",
                    "user": "jack",
                    "kind": "chat",
                })
                await collector.wait_until(
                    lambda messages: bool(_on(messages, "orin/v1/chat/output", turn_id="wire-1")),
                )
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        outputs = _on(collector.messages, "orin/v1/chat/output", turn_id="wire-1")
        self.assertEqual(outputs[-1]["text"], "Hello there")
        self.assertEqual(outputs[-1]["source"], "sc-brain")
        self.assertFalse(outputs[-1]["cancelled"])
        self.assertIn("T", outputs[-1]["ts"])
        streams = _on(collector.messages, "orin/v1/chat/stream", turn_id="wire-1")
        self.assertTrue(any(item["done"] for item in streams))
        self.assertTrue(any(item["token"] == "Hello there" for item in streams))
        system = llm.calls[0]["messages"][0]["content"]
        self.assertTrue("AEDT" in system or "AEST" in system)
        health = _on(collector.messages, "orin/v1/health/sc-brain", status="healthy")
        self.assertEqual(health[0]["source"], "sc-brain")
        self.assertNotIn("turn_id", health[0])

    async def test_cancel_on_the_broker(self):
        llm = ScriptedLLM()
        hold = asyncio.Event()
        llm.scripts["wait"] = [("block", hold), "nope"]
        async with Mosquitto() as broker, Collector(broker.port, ORIN_TOPICS) as collector:
            ready = asyncio.Event()
            brain = ConversationBrain(llm, port=broker.port)
            task = asyncio.create_task(brain.serve(ready))
            try:
                await asyncio.wait_for(ready.wait(), 3)
                await collector.publish("orin/v1/chat/input", {
                    "turn_id": "wire-cancel",
                    "ts": format_ts(),
                    "source": "sc-gateway",
                    "text": "wait",
                    "kind": "chat",
                })
                await self._wait(lambda: any(call["user"] == "wait" for call in llm.calls))
                await collector.publish("orin/v1/turn/cancel", {
                    "turn_id": "wire-cancel",
                    "ts": format_ts(),
                    "source": "sc-gateway",
                    "reason": "barge-in",
                })
                await collector.wait_until(
                    lambda messages: any(
                        item.get("cancelled") for item in _on(messages, "orin/v1/chat/output", turn_id="wire-cancel")
                    ),
                    timeout=1,
                )
            finally:
                hold.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(llm.closed[0], "wait")

    async def test_voice_queue_deadline_is_spoken(self):
        llm = ScriptedLLM()
        llm.scripts["first"] = [("sleep", 0.45), "one"]
        llm.scripts["second"] = ["two"]
        limits = TurnLimits.standard()
        limits = TurnLimits(
            queue_s={**limits.queue_s, "voice": 0.15},
            generation_s=dict(limits.generation_s),
            max_tokens=dict(limits.max_tokens),
        )
        async with Mosquitto() as broker, Collector(broker.port, ORIN_TOPICS) as collector:
            ready = asyncio.Event()
            brain = ConversationBrain(llm, port=broker.port, limits=limits)
            task = asyncio.create_task(brain.serve(ready))
            try:
                await asyncio.wait_for(ready.wait(), 3)
                await collector.publish("orin/v1/voice/transcript", {
                    "turn_id": "vq-1",
                    "ts": format_ts(),
                    "source": "sc-speech",
                    "text": "first",
                })
                await self._wait(lambda: any(call["user"] == "first" for call in llm.calls))
                await collector.publish("orin/v1/voice/transcript", {
                    "turn_id": "vq-2",
                    "ts": format_ts(),
                    "source": "sc-speech",
                    "text": "second",
                })
                await collector.wait_until(
                    lambda messages: bool(_on(messages, "orin/v1/tts/say", turn_id="vq-2")),
                    timeout=2,
                )
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        spoken = _on(collector.messages, "orin/v1/tts/say", turn_id="vq-2")
        self.assertEqual(spoken[-1]["text"], "Sorry, I missed that.")
        self.assertEqual([call["user"] for call in llm.calls], ["first"])

    async def test_invalid_input_is_ignored(self):
        llm = ScriptedLLM(["nope"])
        async with Mosquitto() as broker, Collector(broker.port, ORIN_TOPICS) as collector:
            ready = asyncio.Event()
            brain = ConversationBrain(llm, port=broker.port)
            task = asyncio.create_task(brain.serve(ready))
            try:
                await asyncio.wait_for(ready.wait(), 3)
                await collector.publish("orin/v1/chat/input", {"text": "missing turn id"})
                await asyncio.sleep(0.2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(llm.calls, [])

    async def _wait(self, predicate, timeout: float = 1.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("condition was not met")


class CompatMqttTests(unittest.IsolatedAsyncioTestCase):
    async def test_persona_input_reaches_orin_and_does_not_loop(self):
        async with Mosquitto() as broker, Collector(broker.port, ORIN_TOPICS) as collector:
            ready = asyncio.Event()
            bridge = CompatBridge(port=broker.port)
            task = asyncio.create_task(bridge.serve(ready))
            try:
                await asyncio.wait_for(ready.wait(), 3)
                subscribed = [topic for topic, _qos in bridge.subscriptions()]
                self.assertIn("sentient/persona/chat/input", subscribed)
                await collector.publish("sentient/persona/chat/input", {
                    "text": "Are you there?",
                    "user": "jack",
                    "source": "airi",
                    "timestamp": 1763337600,
                })
                await collector.wait_until(
                    lambda messages: bool(_on(messages, "orin/v1/chat/input")),
                )
                bridged = _on(collector.messages, "orin/v1/chat/input")
                self.assertEqual(len(bridged), 1)
                self.assertEqual(bridged[0]["text"], "Are you there?")
                self.assertEqual(bridged[0]["source"], "airi")
                self.assertEqual(bridged[0]["bridge"], "sc-compat")
                self.assertTrue(bridged[0]["turn_id"])
                self.assertIn("+", bridged[0]["ts"])
                await asyncio.sleep(0.2)
                self.assertEqual(len(_on(collector.messages, "orin/v1/chat/input")), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_orin_output_reaches_legacy_avatar_topics(self):
        async with Mosquitto() as broker, Collector(broker.port, ORIN_TOPICS) as collector:
            ready = asyncio.Event()
            bridge = CompatBridge(port=broker.port)
            task = asyncio.create_task(bridge.serve(ready))
            try:
                await asyncio.wait_for(ready.wait(), 3)
                await collector.publish("orin/v1/chat/output", {
                    "turn_id": "out-1",
                    "ts": format_ts(),
                    "source": "sc-brain",
                    "text": "On my way.",
                    "emotion": "confident",
                    "user": "jack",
                })
                await collector.wait_until(
                    lambda messages: bool(_on(messages, "sentient/persona/response", turn_id="out-1")),
                )
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        for topic in ("sentient/chat/output", "sentient/persona/chat/output", "sentient/persona/response"):
            payloads = _on(collector.messages, topic, turn_id="out-1")
            self.assertEqual(len(payloads), 1, topic)
            self.assertEqual(payloads[0]["text"], "On my way.")
            self.assertEqual(payloads[0]["emotion"], "confident")
            self.assertEqual(payloads[0]["bridge"], "sc-compat")
        self.assertEqual(len(_on(collector.messages, "orin/v1/chat/output", turn_id="out-1")), 1)

    async def test_legacy_stop_cancels_the_active_voice_turn(self):
        bridge = CompatBridge()
        mapped = bridge.translate("sentient/tts/stop", {"reason": "wake_word_interrupt", "source": "voice"})
        self.assertEqual(len(mapped), 1)
        topic, payload, qos, retain = mapped[0]
        self.assertEqual(topic, "orin/v1/turn/cancel")
        self.assertEqual(payload["turn_id"], "*")
        self.assertEqual(payload["reason"], "barge-in")
        self.assertEqual(qos, 1)
        self.assertFalse(retain)
        again = bridge.translate(topic, payload)
        self.assertEqual(again, [])

    async def test_tts_state_fans_out_to_the_legacy_playback_topics(self):
        bridge = CompatBridge()
        started = bridge.translate("orin/v1/tts/state", {
            "turn_id": "t-1",
            "ts": format_ts(),
            "source": "sc-speech",
            "state": "started",
        })
        topics = sorted(item[0] for item in started)
        self.assertEqual(topics, ["sentient/tts/started", "sentient/tts/status"])
        completed = bridge.translate("orin/v1/tts/state", {
            "turn_id": "t-1",
            "ts": format_ts(),
            "source": "sc-speech",
            "state": "completed",
        })
        self.assertEqual(
            sorted(item[0] for item in completed),
            ["sentient/tts/completed", "sentient/tts/status"],
        )

    def test_avatar_emotion_reaches_legacy_expression_topics(self):
        bridge = CompatBridge()
        mapped = bridge.translate("orin/v1/avatar/state", {
            "turn_id": "a-1",
            "ts": format_ts(),
            "source": "sc-brain",
            "state": "speaking",
            "emotion": "confident",
        })
        topics = sorted(item[0] for item in mapped)
        self.assertEqual(
            topics,
            ["sentient/avatar/expression", "sentient/avatar/state", "sentient/persona/emotion"],
        )
        expression = next(item for item in mapped if item[0] == "sentient/avatar/expression")
        self.assertEqual(expression[1]["emotion"], "confident")
        self.assertEqual(expression[1]["bridge"], "sc-compat")

    async def test_airi_path_through_compat_and_brain(self):
        llm = ScriptedLLM(["Systems online."])
        async with Mosquitto() as broker, Collector(broker.port, ORIN_TOPICS) as collector:
            brain_ready = asyncio.Event()
            compat_ready = asyncio.Event()
            brain = ConversationBrain(llm, port=broker.port)
            bridge = CompatBridge(port=broker.port)
            brain_task = asyncio.create_task(brain.serve(brain_ready))
            compat_task = asyncio.create_task(bridge.serve(compat_ready))
            try:
                await asyncio.wait_for(brain_ready.wait(), 3)
                await asyncio.wait_for(compat_ready.wait(), 3)
                await collector.publish("sentient/persona/chat/input", {
                    "text": "Status?",
                    "user": "jack",
                    "source": "airi",
                })
                await collector.wait_until(
                    lambda messages: bool(_on(messages, "sentient/persona/response")),
                    timeout=3,
                )
            finally:
                brain_task.cancel()
                compat_task.cancel()
                await asyncio.gather(brain_task, compat_task, return_exceptions=True)
        response = _on(collector.messages, "sentient/persona/response")
        self.assertEqual(response[-1]["text"], "Systems online.")
        turn_id = response[-1]["turn_id"]
        orin_out = _on(collector.messages, "orin/v1/chat/output", turn_id=turn_id)
        self.assertEqual(orin_out[-1]["text"], "Systems online.")
        self.assertEqual(orin_out[-1]["source"], "sc-brain")
        orin_in = _on(collector.messages, "orin/v1/chat/input")
        self.assertEqual(orin_in[0]["turn_id"], turn_id)
        self.assertEqual(llm.calls[0]["user"], "Status?")


if __name__ == "__main__":
    unittest.main()
