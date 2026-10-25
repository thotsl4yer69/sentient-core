"""Sentient voice modules.

Avoid importing GPU/wake/mic dependencies for lightweight STT/TTS workers
and deterministic skill tests.
"""

__all__ = ["WakeWordService", "VoicePipeline"]


def __getattr__(name):
    if name == "WakeWordService":
        from .wake_word import WakeWordService
        return WakeWordService
    if name == "VoicePipeline":
        from .pipeline import VoicePipeline
        return VoicePipeline
    raise AttributeError(name)
