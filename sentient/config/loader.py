"""
Configuration loader with TOML file parsing and environment variable overrides.
"""
import os
import logging
from pathlib import Path
from typing import Optional, Any, Dict

# Try Python 3.11+ tomllib first, fallback to tomli for older versions
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ImportError:
        raise ImportError(
            "Neither tomllib (Python 3.11+) nor tomli package found. "
            "Install tomli: pip install tomli"
        )

from .models import (
    SentientConfig,
    MQTTConfig,
    RedisConfig,
    OllamaConfig,
    WhisperConfig,
    PiperConfig,
    NtfyConfig,
    WakeWordConfig,
    ConversationConfig,
    MemoryConfig,
    ContemplationConfig,
    PerceptionConfig,
    AvatarConfig,
    WebChatConfig,
)

logger = logging.getLogger(__name__)

_config: Optional[SentientConfig] = None

CONFIG_PATHS = [
    Path("/opt/sentient-core/config/cortana.toml"),
    Path("/opt/sentient-core/sentient/config/cortana.toml"),
    Path.home() / ".config" / "sentient" / "cortana.toml",
]


def _load_toml(path: Path) -> Dict[str, Any]:
    """Load TOML file and return parsed dict."""
    with open(path, "rb") as f:
        return tomllib.load(f)


def _apply_env_overrides(config: SentientConfig) -> SentientConfig:
    """
    Override config with environment variables.
    Format: SENTIENT_SECTION_KEY
    Example: SENTIENT_MQTT_PASSWORD overrides config.mqtt.password
    """
    env_map = {
        # MQTT overrides
        "SENTIENT_MQTT_BROKER": lambda v: setattr(config.mqtt, "broker", v),
        "SENTIENT_MQTT_PORT": lambda v: setattr(config.mqtt, "port", int(v)),
        "SENTIENT_MQTT_USERNAME": lambda v: setattr(config.mqtt, "username", v),
        "SENTIENT_MQTT_PASSWORD": lambda v: setattr(config.mqtt, "password", v),

        # Redis overrides
        "SENTIENT_REDIS_HOST": lambda v: setattr(config.redis, "host", v),
        "SENTIENT_REDIS_PORT": lambda v: setattr(config.redis, "port", int(v)),
        "SENTIENT_REDIS_DB": lambda v: setattr(config.redis, "db", int(v)),

        # Ollama overrides
        "SENTIENT_OLLAMA_HOST": lambda v: setattr(config.ollama, "host", v),
        "SENTIENT_OLLAMA_MODEL": lambda v: setattr(config.ollama, "model", v),

        # Ntfy overrides
        "SENTIENT_NTFY_SERVER": lambda v: setattr(config.ntfy, "server", v),
        "SENTIENT_NTFY_TOPIC": lambda v: setattr(config.ntfy, "topic", v),

        # Wake word overrides
        "SENTIENT_WAKE_WORD_MODEL": lambda v: setattr(config.wake_word, "model", v),
        "SENTIENT_WAKE_WORD_SENSITIVITY": lambda v: setattr(config.wake_word, "sensitivity", float(v)),

        # Conversation overrides
        "SENTIENT_CONVERSATION_MAX_HISTORY": lambda v: setattr(config.conversation, "max_history", int(v)),
        "SENTIENT_CONVERSATION_IDLE_TIMEOUT": lambda v: setattr(config.conversation, "idle_timeout", int(v)),

        # Service port overrides
        "SENTIENT_MEMORY_PORT": lambda v: setattr(config.memory, "port", int(v)),
        "SENTIENT_CONTEMPLATION_PORT": lambda v: setattr(config.contemplation, "port", int(v)),
        "SENTIENT_PERCEPTION_PORT": lambda v: setattr(config.perception, "port", int(v)),
        "SENTIENT_AVATAR_BRIDGE_WS_PORT": lambda v: setattr(config.avatar, "bridge_ws_port", int(v)),
        "SENTIENT_WEB_CHAT_PORT": lambda v: setattr(config.web_chat, "port", int(v)),
    }

    for env_var, setter in env_map.items():
        value = os.environ.get(env_var)
        if value is not None:
            try:
                setter(value)
                logger.debug(f"Config override from env: {env_var}")
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to apply env override {env_var}={value}: {e}")

    return config


def _toml_to_config(data: Dict[str, Any]) -> SentientConfig:
    """Convert TOML dict to SentientConfig dataclass."""
    config = SentientConfig()

    # Map TOML section names to config sub-objects
    section_map = {
        "mqtt": config.mqtt,
        "redis": config.redis,
        "ollama": config.ollama,
        "whisper": config.whisper,
        "piper": config.piper,
        "ntfy": config.ntfy,
        "wake_word": config.wake_word,
        "conversation": config.conversation,
        "memory": config.memory,
        "contemplation": config.contemplation,
        "perception": config.perception,
        "avatar": config.avatar,
        "web_chat": config.web_chat,
    }

    for section_name, section_obj in section_map.items():
        if section_name in data:
            for k, v in data[section_name].items():
                if hasattr(section_obj, k):
                    setattr(section_obj, k, v)

    return config


def load_config(config_path: Optional[Path] = None) -> SentientConfig:
    """
    Load configuration from TOML file with environment variable overrides.

    Args:
        config_path: Optional explicit path to config file. If None, searches default paths.

    Returns:
        SentientConfig instance with loaded configuration.
    """
    global _config

    if config_path:
        paths = [config_path]
    else:
        paths = CONFIG_PATHS

    data = {}
    for path in paths:
        if path.exists():
            try:
                data = _load_toml(path)
                logger.info(f"Loaded config from {path}")
                break
            except Exception as e:
                logger.error(f"Failed to load config from {path}: {e}")
                continue
    else:
        logger.warning("No config file found, using defaults")

    config = _toml_to_config(data)
    config = _apply_env_overrides(config)
    _config = config
    return config


def get_config() -> SentientConfig:
    """
    Get cached config or load if not yet loaded.

    Returns:
        SentientConfig instance.
    """
    global _config
    if _config is None:
        _config = load_config()
    return _config
