"""Voice services for Sentient Core.

Provides wake word detection and the full voice interaction pipeline.
"""

from sentient.services.voice.wake_word import WakeWordService
from sentient.services.voice.pipeline import VoicePipeline

__all__ = ["WakeWordService", "VoicePipeline"]
