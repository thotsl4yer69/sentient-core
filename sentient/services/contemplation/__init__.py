"""Contemplation service - Multi-voice contemplative reasoning engine."""

from .engine import (
    ContemplationEngine,
    ContemplationResult,
    Voice,
    InputType,
    EmotionCategory,
    EmotionState,
    VoicePerspective,
    ExpressionHints,
    OllamaClient,
    MemoryStore,
)

__all__ = [
    "ContemplationEngine",
    "ContemplationResult",
    "Voice",
    "InputType",
    "EmotionCategory",
    "EmotionState",
    "VoicePerspective",
    "ExpressionHints",
    "OllamaClient",
    "MemoryStore",
]
