"""A subscription log is only honest when the subscribe call above it is live."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CHECKED = [
    ROOT / "services/conversation.py",
    ROOT / "sentient/services/conversation/brain.py",
    ROOT / "sentient/services/compat/bridge.py",
]


def _previous_code_line(lines: list[str], index: int) -> str:
    cursor = index - 1
    while cursor >= 0 and not lines[cursor].strip():
        cursor -= 1
    if cursor < 0:
        return ""
    return lines[cursor]


class SubscribeLogTests(unittest.TestCase):
    def test_subscribed_log_follows_a_real_subscribe_call(self):
        for path in CHECKED:
            lines = path.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                if "Subscribed to" not in line or line.lstrip().startswith("#"):
                    continue
                previous = _previous_code_line(lines, index)
                self.assertFalse(
                    previous.lstrip().startswith("#"),
                    f"{path.name}:{index + 1} logs Subscribed after a comment: {previous}",
                )
                self.assertIn(
                    "subscribe(",
                    previous,
                    f"{path.name}:{index + 1} logs Subscribed without a subscribe call",
                )

    def test_legacy_persona_input_is_not_claimed(self):
        text = (ROOT / "services/conversation.py").read_text(encoding="utf-8")
        self.assertNotIn('logger.info(f"Subscribed to {TOPIC_CHAT_INPUT}")', text)
        self.assertNotIn("await self.mqtt_client.subscribe(TOPIC_CHAT_INPUT)", text)
        self.assertIn("sc-compat", text)
        self.assertIn("orin/v1/chat/input", text)


if __name__ == "__main__":
    unittest.main()
