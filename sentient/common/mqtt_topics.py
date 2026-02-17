"""Canonical MQTT topic constants for Sentient Core.

All services MUST use these constants instead of hardcoding topic strings.
Topic namespace: sentient/
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
