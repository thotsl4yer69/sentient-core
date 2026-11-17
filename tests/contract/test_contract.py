"""The sc/v1 document matches the redesign and the generated Python constants."""

from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sentient.common import mqtt_topics
from sentient.common.mqtt_topics import orin_health_topic
from sentient.services.conversation.contract import (
    assert_constants_match,
    extract_generated_block,
    format_ts,
    load_contract,
    local_time_phrase,
    render_generated_block,
    validate_payload,
)

ROOT = Path(__file__).resolve().parents[2]

# Approved topic table. Prefix is sc/v1, the contract WS5 and WS7 import.
DESIGN = [
    ("voice/wake", ["sc-speech"], ["sc-brain", "sc-gateway"], 1, False, False, "required"),
    ("voice/transcript", ["sc-speech"], ["sc-brain"], 1, False, False, "required"),
    ("chat/input", ["sc-gateway", "sc-buddy"], ["sc-brain"], 1, False, False, "required"),
    ("chat/stream", ["sc-brain"], ["sc-gateway", "sc-buddy"], 0, False, False, "required"),
    ("chat/output", ["sc-brain"], ["sc-gateway", "sc-buddy", "sc-memory"], 1, False, False, "required"),
    ("turn/cancel", ["sc-speech", "sc-gateway"], ["sc-brain", "sc-speech"], 1, False, False, "required"),
    ("tts/say", ["sc-brain"], ["sc-speech"], 1, False, False, "required"),
    ("tts/state", ["sc-speech"], ["sc-brain", "sc-gateway"], 1, False, False, "required"),
    ("avatar/state", ["sc-brain"], ["sc-gateway"], 0, True, False, "optional"),
    ("health/{service}", ["sc-*"], ["sc-health"], 1, True, True, "absent"),
]

LEGACY = {
    "CHAT_INPUT": "sentient/chat/input",
    "CHAT_OUTPUT": "sentient/chat/output",
    "PERSONA_CHAT_INPUT": "sentient/persona/chat/input",
    "PERSONA_RESPONSE": "sentient/persona/response",
    "WAKE_WORD_DETECTED": "sentient/wake/detected",
    "VOICE_TRANSCRIPTION": "sentient/voice/transcription",
    "TTS_SPEAK": "sentient/tts/request",
    "TTS_STOP": "sentient/tts/stop",
    "CHAT_STREAM": "sentient/chat/stream",
    "AVATAR_STATE": "sentient/avatar/state",
    "AVATAR_EXPRESSION": "sentient/avatar/expression",
}


class ContractDocumentTests(unittest.TestCase):
    def setUp(self):
        self.contract = load_contract()

    def test_design_table_is_the_contract(self):
        topics = self.contract["topics"]
        self.assertEqual([topic["id"] for topic in topics], [row[0] for row in DESIGN])
        for topic, row in zip(topics, DESIGN):
            ident, publishers, subscribers, qos, retain, lwt, turn_policy = row
            self.assertEqual(topic["topic"], f"sc/v1/{ident}")
            self.assertEqual(topic["publishers"], publishers)
            self.assertEqual(topic["subscribers"], subscribers)
            self.assertEqual(topic["qos"], qos)
            self.assertEqual(topic["retain"], retain)
            self.assertEqual(topic["lwt"], lwt)
            self.assertEqual(topic["turn_id"], turn_policy)
            required = topic["payload"]["required"]
            self.assertIn("ts", required)
            self.assertIn("source", required)
            if turn_policy == "required":
                self.assertIn("turn_id", required)
            if turn_policy == "absent":
                self.assertNotIn("turn_id", required)

    def test_persona_chat_input_is_bridged_onto_orin(self):
        match = [
            bridge for bridge in self.contract["bridges"]
            if bridge["legacy"] == "sentient/persona/chat/input"
        ]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["orin"], "sc/v1/chat/input")
        self.assertIn(match[0]["direction"], ("both", "to_orin"))

    def test_generated_block_matches_the_source_file(self):
        source = (ROOT / "sentient/common/mqtt_topics.py").read_text(encoding="utf-8")
        self.assertEqual(extract_generated_block(source), render_generated_block(self.contract))
        assert_constants_match(self.contract, mqtt_topics)

    def test_legacy_constants_keep_their_topics(self):
        for name, topic in LEGACY.items():
            self.assertEqual(getattr(mqtt_topics, name), topic)

    def test_health_topic_rejects_wildcards(self):
        self.assertEqual(orin_health_topic("sc-brain"), "sc/v1/health/sc-brain")
        for bad in ("", "sc/brain", "sc-brain/+", "sc-brain/#"):
            with self.assertRaises(ValueError):
                orin_health_topic(bad)

    def test_payload_rules(self):
        good = {"turn_id": "turn-1", "ts": format_ts(), "source": "sc-speech", "text": "hi"}
        self.assertEqual(validate_payload("voice/transcript", good, self.contract), [])
        missing = dict(good)
        del missing["turn_id"]
        self.assertTrue(validate_payload("voice/transcript", missing, self.contract))
        health = {"ts": format_ts(), "source": "sc-brain", "status": "healthy"}
        self.assertEqual(validate_payload("health/{service}", health, self.contract), [])
        health["turn_id"] = "turn-1"
        self.assertTrue(validate_payload("health/{service}", health, self.contract))
        cancel = {"turn_id": "*", "ts": format_ts(), "source": "sc-speech", "reason": "barge-in"}
        self.assertEqual(validate_payload("turn/cancel", cancel, self.contract), [])
        self.assertTrue(validate_payload("chat/input", {**good, "turn_id": "*"}, self.contract))

    def test_adapter_signature_is_what_the_brain_calls(self):
        path = ROOT / "sentient/services/contemplation/local_openai.py"
        spec = importlib.util.spec_from_file_location("local_openai_contract", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        chat = module.LocalOpenAIClient.chat_stream
        generate = module.LocalOpenAIClient.generate
        for name in ("model", "messages", "temperature", "max_tokens", "timeout", "stop"):
            self.assertIn(name, chat.__code__.co_varnames)
        for name in ("model", "prompt", "system", "temperature", "max_tokens", "timeout", "stop"):
            self.assertIn(name, generate.__code__.co_varnames)


class MelbourneTimeTests(unittest.TestCase):
    def test_both_daylight_saving_changeovers(self):
        april_before = datetime(2026, 4, 4, 15, 30, tzinfo=timezone.utc)
        april_after = datetime(2026, 4, 4, 16, 30, tzinfo=timezone.utc)
        october_before = datetime(2026, 10, 3, 15, 30, tzinfo=timezone.utc)
        october_after = datetime(2026, 10, 3, 16, 30, tzinfo=timezone.utc)
        self.assertIn("AEDT", local_time_phrase(april_before))
        self.assertIn("AEST", local_time_phrase(april_after))
        self.assertIn("AEST", local_time_phrase(october_before))
        self.assertIn("AEDT", local_time_phrase(october_after))
        self.assertIn("+11:00", format_ts(april_before))
        self.assertIn("+10:00", format_ts(april_after))
        self.assertIn("+10:00", format_ts(october_before))
        self.assertIn("+11:00", format_ts(october_after))


if __name__ == "__main__":
    unittest.main()
