"""sc/v1 topic names from the approved Orin stack redesign v2.

WS6 owns contract/mqtt.yaml and sentient/common/mqtt_topics.py. Those names
were not in this tree when the gateway was written, so publish and subscribe
use the contract strings from the design. Every payload that belongs to a
turn carries turn_id, ts, and source.
"""

from __future__ import annotations

PREFIX = "sc/v1"
SOURCE = "sc-gateway"


def topic(suffix: str) -> str:
    if not suffix or suffix.startswith("/") or "#" in suffix or "+" in suffix:
        raise ValueError(f"gateway topic must be a concrete sc/v1 path, got {suffix!r}")
    return f"{PREFIX}/{suffix}"


CHAT_INPUT = topic("chat/input")
CHAT_STREAM = topic("chat/stream")
CHAT_OUTPUT = topic("chat/output")
TURN_CANCEL = topic("turn/cancel")
VOICE_WAKE = topic("voice/wake")
TTS_STATE = topic("tts/state")
AVATAR_STATE = topic("avatar/state")
HEALTH = topic("health/gateway")

# QoS and retain follow the design table. The gateway only touches topics
# its broker ACL allows.
SUBSCRIPTIONS: tuple[tuple[str, int], ...] = (
    (VOICE_WAKE, 1),
    (CHAT_STREAM, 0),
    (CHAT_OUTPUT, 1),
    (TTS_STATE, 1),
    (AVATAR_STATE, 0),
)

PUBLISH_QOS = {
    CHAT_INPUT: 1,
    TURN_CANCEL: 1,
    HEALTH: 1,
}
