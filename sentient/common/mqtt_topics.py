"""Canonical MQTT topic constants for Sentient Core.

All services MUST use these constants instead of hardcoding topic strings.
Legacy namespace: sentient/
Current contract namespace: sc/v1 (see contract/mqtt.yaml). The generated
block below is checked against that file by tests/contract.
"""

# Chat / Conversation
CHAT_INPUT = "sentient/chat/input"
CHAT_OUTPUT = "sentient/chat/output"
CONVERSATION_STATE = "sentient/conversation/state"

# Voice Pipeline
VOICE_TRANSCRIPTION = "sentient/voice/transcription"
WAKE_WORD_DETECTED = "sentient/wake/detected"
TTS_SPEAK = "sentient/tts/request"
TTS_STATUS = "sentient/tts/status"
TTS_OUTPUT = "sentient/tts/output"
TTS_STARTED = "sentient/tts/started"
TTS_COMPLETED = "sentient/tts/completed"
TTS_STOP = "sentient/tts/stop"
TTS_CONTROL = "sentient/conversation/tts/control"
STT_TRANSCRIBE = "sentient/stt/transcribe"
STT_AUDIO_INPUT = "sentient/stt/audio/input"
STT_OUTPUT = "sentient/stt/output"
PERSONA_CHAT_INPUT = "sentient/persona/chat/input"
PERSONA_RESPONSE = "sentient/persona/response"

# Avatar
AVATAR_EXPRESSION = "sentient/avatar/expression"
AVATAR_STATE = "sentient/avatar/state"
AVATAR_TEXT = "sentient/avatar/text"
AVATAR_PHONEMES = "sentient/avatar/phonemes"
AVATAR_THINKING = "sentient/avatar/thinking"
AVATAR_ATTENTION = "sentient/avatar/attention"
AVATAR_SPEAKING = "sentient/avatar/speaking"
AVATAR_IDLE = "sentient/avatar/idle"

# Perception
PERCEPTION_STATE = "sentient/perception/state"
VISION_DETECTION = "sentient/vision/detection"
RF_DETECTION = "sentient/rf/detection"
NETWORK_STATE = "sentient/perception/network"
NETWORK_DEVICE_ARRIVED = "sentient/network/device/arrived"
NETWORK_DEVICE_DEPARTED = "sentient/network/device/departed"

# System
SYSTEM_STATUS = "sentient/system/status"
SYSTEM_HEALTH = "sentient/system/health"

# Proactive
PROACTIVE_TRIGGER = "sentient/proactive/trigger"
PROACTIVE_NOTIFICATION = "sentient/proactive/notification"

# Notifications
NOTIFICATION_SEND = "sentient/notifications/send"

# Chat Streaming
CHAT_STREAM = "sentient/chat/stream"

# Persona / Contemplation
PERSONA_STATE = "sentient/persona/state"
PERSONA_THOUGHT_STREAM = "sentient/persona/thought_stream"
PERSONA_EMOTION = "sentient/persona/emotion"

# Memory
MEMORY_EVENT = "sentient/memory/event"

# Reminders
REMINDER_DUE = "sentient/reminder/due"
REMINDER_SET = "sentient/reminder/set"

# Sensor (wildcard)
SENSOR_VISION_DETECTION = "sentient/sensor/vision/+/detection"

# Feedback
FEEDBACK_RECEIVED = "sentient/feedback/received"


# BEGIN GENERATED ORIN/V1 - from contract/mqtt.yaml. Do not edit by hand.
ORIN_PREFIX = "sc/v1"

ORIN_VOICE_WAKE = "sc/v1/voice/wake"
ORIN_VOICE_TRANSCRIPT = "sc/v1/voice/transcript"
ORIN_CHAT_INPUT = "sc/v1/chat/input"
ORIN_CHAT_STREAM = "sc/v1/chat/stream"
ORIN_CHAT_OUTPUT = "sc/v1/chat/output"
ORIN_TURN_CANCEL = "sc/v1/turn/cancel"
ORIN_TTS_SAY = "sc/v1/tts/say"
ORIN_TTS_STATE = "sc/v1/tts/state"
ORIN_AVATAR_STATE = "sc/v1/avatar/state"
ORIN_HEALTH = "sc/v1/health/{service}"

ORIN_TOPICS = {
    "voice/wake": ORIN_VOICE_WAKE,
    "voice/transcript": ORIN_VOICE_TRANSCRIPT,
    "chat/input": ORIN_CHAT_INPUT,
    "chat/stream": ORIN_CHAT_STREAM,
    "chat/output": ORIN_CHAT_OUTPUT,
    "turn/cancel": ORIN_TURN_CANCEL,
    "tts/say": ORIN_TTS_SAY,
    "tts/state": ORIN_TTS_STATE,
    "avatar/state": ORIN_AVATAR_STATE,
    "health/{service}": ORIN_HEALTH,
}

ORIN_QOS = {
    "voice/wake": 1,
    "voice/transcript": 1,
    "chat/input": 1,
    "chat/stream": 0,
    "chat/output": 1,
    "turn/cancel": 1,
    "tts/say": 1,
    "tts/state": 1,
    "avatar/state": 0,
    "health/{service}": 1,
}

ORIN_RETAIN = {
    "voice/wake": False,
    "voice/transcript": False,
    "chat/input": False,
    "chat/stream": False,
    "chat/output": False,
    "turn/cancel": False,
    "tts/say": False,
    "tts/state": False,
    "avatar/state": True,
    "health/{service}": True,
}

ORIN_LWT = {
    "voice/wake": False,
    "voice/transcript": False,
    "chat/input": False,
    "chat/stream": False,
    "chat/output": False,
    "turn/cancel": False,
    "tts/say": False,
    "tts/state": False,
    "avatar/state": False,
    "health/{service}": True,
}
# END GENERATED ORIN/V1


def orin_health_topic(service: str) -> str:
    """Retained health topic for one sc-* service. Unexpected disconnects use it as the LWT."""
    if not isinstance(service, str) or not service or any(char in service for char in "/+#"):
        raise ValueError(f"invalid health service name: {service!r}")
    return f"{ORIN_PREFIX}/health/{service}"
