"""
Configuration dataclass models for Sentient Core.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MQTTConfig:
    """MQTT broker configuration."""
    broker: str = "localhost"
    port: int = 1883
    username: str = "sentient"
    password: str = ""  # loaded from env


@dataclass
class RedisConfig:
    """Redis server configuration."""
    host: str = "localhost"
    port: int = 6379
    db: int = 0


@dataclass
class OllamaConfig:
    """Ollama LLM configuration."""
    host: str = "http://localhost:11434"
    model: str = "llama3.2:3b"
    timeout: float = 120.0
    default_max_tokens: int = 300
    default_temperature: float = 0.7
    think_enabled: bool = False
    num_ctx: int = 2048


@dataclass
class WhisperConfig:
    """Whisper speech-to-text configuration."""
    service_topic: str = "sentient/stt/transcribe"


@dataclass
class PiperConfig:
    """Piper text-to-speech configuration."""
    service_topic: str = "sentient/tts/synthesize"


@dataclass
class NtfyConfig:
    """Ntfy notification service configuration."""
    server: str = "https://ntfy.sh"
    topic: str = ""


@dataclass
class WakeWordConfig:
    """Wake word detection configuration."""
    model: str = "hey_cortana"
    sensitivity: float = 0.5


@dataclass
class ConversationConfig:
    """Conversation management configuration."""
    max_history: int = 20
    idle_timeout: int = 1800  # 30 minutes in seconds


@dataclass
class MemoryConfig:
    """Memory service configuration."""
    port: int = 8001
    embedding_model: str = "all-MiniLM-L6-v2"


@dataclass
class ContemplationConfig:
    """Contemplation service configuration."""
    port: int = 8002
    default_mode: str = "fast"  # "fast" or "full"


@dataclass
class PerceptionConfig:
    """Perception service configuration."""
    port: int = 8003
    world_state_interval: float = 5.0


@dataclass
class AvatarConfig:
    """Avatar service configuration."""
    bridge_ws_port: int = 9001


@dataclass
class WebChatConfig:
    """Web chat interface configuration."""
    port: int = 3001


@dataclass
class SentientConfig:
    """Root configuration object containing all subsystem configs."""
    mqtt: MQTTConfig = field(default_factory=MQTTConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    piper: PiperConfig = field(default_factory=PiperConfig)
    ntfy: NtfyConfig = field(default_factory=NtfyConfig)
    wake_word: WakeWordConfig = field(default_factory=WakeWordConfig)
    conversation: ConversationConfig = field(default_factory=ConversationConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    contemplation: ContemplationConfig = field(default_factory=ContemplationConfig)
    perception: PerceptionConfig = field(default_factory=PerceptionConfig)
    avatar: AvatarConfig = field(default_factory=AvatarConfig)
    web_chat: WebChatConfig = field(default_factory=WebChatConfig)
