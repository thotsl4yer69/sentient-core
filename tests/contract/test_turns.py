"""Turn ids, cancellation, preemption, and deadlines without a broker."""

from __future__ import annotations

import asyncio
import unittest

from sentient.services.conversation.contract import format_ts, load_contract, validate_payload
from sentient.services.conversation.turns import STOP_SEQUENCES, TurnEngine, TurnLimits
from tests.contract.fakes import ScriptedLLM


def _limits(**overrides) -> TurnLimits:
    base = TurnLimits.standard()
    queue = dict(base.queue_s)
    generation = dict(base.generation_s)
    tokens = dict(base.max_tokens)
    queue.update(overrides.get("queue_s", {}))
    generation.update(overrides.get("generation_s", {}))
    tokens.update(overrides.get("max_tokens", {}))
    return TurnLimits(queue, generation, tokens)


class TurnEngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.contract = load_contract()
        self.events: list[tuple[str, dict]] = []

    async def _publish(self, topic_id: str, payload: dict) -> None:
        errors = validate_payload(topic_id, payload, self.contract)
        if errors:
            raise AssertionError(f"{topic_id} payload invalid: {errors} {payload}")
        self.events.append((topic_id, payload))

    def _engine(self, llm: ScriptedLLM, **limit_overrides) -> TurnEngine:
        return TurnEngine(llm, self._publish, limits=_limits(**limit_overrides), model="loaded-gguf")

    def _outputs(self, turn_id: str) -> list[dict]:
        return [payload for topic, payload in self.events if topic == "chat/output" and payload["turn_id"] == turn_id]

    async def test_voice_preempts_chat_and_does_not_count_the_attempt(self):
        llm = ScriptedLLM()
        hold = asyncio.Event()
        llm.scripts["remember this"] = [("block", hold), "Remembered."]
        llm.scripts["what time is it"] = ["Six."]
        engine = self._engine(llm)
        await engine.start()
        try:
            chat = await engine.handle_chat_input({
                "turn_id": "chat-1",
                "ts": format_ts(),
                "source": "sc-gateway",
                "text": "remember this",
                "user": "jack",
                "kind": "chat",
            })
            await self._wait(lambda: engine.active is chat)
            voice = await engine.handle_transcript({
                "turn_id": "voice-1",
                "ts": format_ts(),
                "source": "sc-speech",
                "text": "what time is it",
            })
            await engine.wait_status("voice-1", {"completed"})
            self.assertEqual(chat.attempts, 0)
            self.assertEqual(llm.closed[0], "remember this")
            system = llm.calls[0]["messages"][0]["content"]
            self.assertTrue("AEDT" in system or "AEST" in system)
            self.assertEqual(llm.calls[0]["model"], "loaded-gguf")
            self.assertEqual(llm.calls[0]["stop"], STOP_SEQUENCES)
            hold.set()
            await engine.wait_status("chat-1", {"completed"})
            self.assertEqual(chat.attempts, 1)
            users = [call["user"] for call in llm.calls]
            self.assertEqual(users, ["remember this", "what time is it", "remember this"])
            self.assertEqual(self._outputs("voice-1")[-1]["text"], "Six.")
            self.assertFalse(self._outputs("voice-1")[-1]["cancelled"])
            self.assertEqual(voice.turn_id, "voice-1")
        finally:
            hold.set()
            await engine.stop()

    async def test_cancel_closes_the_stream(self):
        llm = ScriptedLLM()
        hold = asyncio.Event()
        llm.scripts["hold"] = [("block", hold), "late"]
        engine = self._engine(llm)
        await engine.start()
        try:
            await engine.handle_chat_input({
                "turn_id": "c-1",
                "ts": format_ts(),
                "source": "sc-gateway",
                "text": "hold",
                "kind": "chat",
            })
            await self._wait(lambda: engine.active is not None and engine.active.turn_id == "c-1")
            started = asyncio.get_running_loop().time()
            await engine.handle_cancel({
                "turn_id": "c-1",
                "ts": format_ts(),
                "source": "sc-gateway",
                "reason": "barge-in",
            })
            turn = await engine.wait_status("c-1", {"cancelled"}, timeout=0.5)
            elapsed = asyncio.get_running_loop().time() - started
            self.assertLess(elapsed, 0.3)
            self.assertEqual(turn.attempts, 1)
            self.assertEqual(llm.closed, ["hold"])
            output = self._outputs("c-1")[-1]
            self.assertTrue(output["cancelled"])
            self.assertEqual(output["reason"], "barge-in")
            self.assertNotIn("late", output["text"])
        finally:
            hold.set()
            await engine.stop()

    async def test_wildcard_cancel_targets_the_live_voice_turn(self):
        llm = ScriptedLLM()
        hold = asyncio.Event()
        llm.scripts["listening"] = [("block", hold)]
        engine = self._engine(llm)
        await engine.start()
        try:
            await engine.handle_transcript({
                "turn_id": "v-9",
                "ts": format_ts(),
                "source": "sc-speech",
                "text": "listening",
            })
            await self._wait(lambda: engine.active is not None)
            await engine.handle_cancel({
                "turn_id": "*",
                "ts": format_ts(),
                "source": "legacy",
                "reason": "barge-in",
            })
            await engine.wait_status("v-9", {"cancelled"}, timeout=0.5)
        finally:
            hold.set()
            await engine.stop()

    async def test_generation_deadline_stops_a_stalled_stream(self):
        llm = ScriptedLLM()
        llm.scripts["slow"] = ["Hi", ("sleep", 5)]
        engine = self._engine(llm, generation_s={"voice": 0.3})
        await engine.start()
        try:
            await engine.handle_transcript({
                "turn_id": "slow-1",
                "ts": format_ts(),
                "source": "sc-speech",
                "text": "slow",
            })
            turn = await engine.wait_status("slow-1", {"failed"}, timeout=2)
            self.assertEqual(turn.attempts, 1)
            output = self._outputs("slow-1")[-1]
            self.assertEqual(output["reason"], "generation_deadline")
            self.assertEqual(output["text"], "Hi")
            spoken = [
                payload for topic, payload in self.events
                if topic == "tts/say" and payload["turn_id"] == "slow-1"
            ]
            self.assertTrue(spoken)
        finally:
            await engine.stop()

    async def test_voice_token_cap(self):
        llm = ScriptedLLM()
        llm.scripts["count"] = ["a", "b", "c", "d"]
        engine = self._engine(llm, max_tokens={"voice": 2})
        await engine.start()
        try:
            await engine.handle_transcript({
                "turn_id": "cap-1",
                "ts": format_ts(),
                "source": "sc-speech",
                "text": "count",
            })
            await engine.wait_status("cap-1", {"completed"})
            self.assertEqual(llm.calls[0]["max_tokens"], 2)
            self.assertEqual(llm.calls[0]["timeout"], TurnLimits.standard().generation_s["voice"])
            tokens = [payload["token"] for topic, payload in self.events if topic == "chat/stream" and payload["token"]]
            self.assertEqual(tokens, ["a", "b"])
            self.assertEqual(self._outputs("cap-1")[-1]["text"], "ab")
        finally:
            await engine.stop()

    async def test_voice_queue_deadline_speaks_a_short_line_without_the_model(self):
        llm = ScriptedLLM()
        llm.scripts["first"] = [("sleep", 0.45), "one"]
        llm.scripts["second"] = ["two"]
        engine = self._engine(llm, queue_s={"voice": 0.15})
        await engine.start()
        try:
            await engine.handle_transcript({
                "turn_id": "q-1",
                "ts": format_ts(),
                "source": "sc-speech",
                "text": "first",
            })
            await self._wait(lambda: engine.active is not None and engine.active.turn_id == "q-1")
            await engine.handle_transcript({
                "turn_id": "q-2",
                "ts": format_ts(),
                "source": "sc-speech",
                "text": "second",
            })
            turn = await engine.wait_status("q-2", {"failed"}, timeout=2)
            self.assertEqual(turn.attempts, 0)
            self.assertEqual([call["user"] for call in llm.calls], ["first"])
            output = self._outputs("q-2")[-1]
            self.assertEqual(output["text"], "Sorry, I missed that.")
            self.assertEqual(output["reason"], "queue_deadline")
            spoken = [
                payload["text"] for topic, payload in self.events
                if topic == "tts/say" and payload["turn_id"] == "q-2"
            ]
            self.assertEqual(spoken, ["Sorry, I missed that."])
        finally:
            await engine.stop()

    async def test_wake_adopts_the_publishers_turn_id(self):
        llm = ScriptedLLM()
        engine = self._engine(llm)
        await engine.start()
        try:
            turn_id = await engine.handle_wake({
                "turn_id": "wake-7",
                "ts": format_ts(),
                "source": "sc-speech",
                "confidence": 0.9,
            })
            self.assertEqual(turn_id, "wake-7")
            states = [payload for topic, payload in self.events if topic == "avatar/state"]
            self.assertEqual(states[-1]["state"], "listening")
            self.assertEqual(states[-1]["turn_id"], "wake-7")
        finally:
            await engine.stop()

    async def _wait(self, predicate, timeout: float = 1.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("condition was not met")


if __name__ == "__main__":
    unittest.main()
