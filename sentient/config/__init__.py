"""
Sentient Core configuration management.

Provides centralized configuration loading from TOML files with environment variable overrides.

Usage:
    from sentient.config import get_config

    config = get_config()
    mqtt_broker = config.mqtt.broker
    ollama_model = config.ollama.model
"""
from .loader import load_config, get_config
from .models import SentientConfig

__all__ = ["load_config", "get_config", "SentientConfig"]
