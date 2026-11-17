"""Conversation service.

``ConversationOrchestrator`` is the existing v2 orchestrator.
``ConversationBrain`` is sc-brain: it owns sc/v1 turns and is loaded lazily
so importing the orchestrator does not open an MQTT session.
"""

from .orchestrator import ConversationOrchestrator

__all__ = ["ConversationOrchestrator", "ConversationBrain"]


def __getattr__(name: str):
    if name == "ConversationBrain":
        from .brain import ConversationBrain
        return ConversationBrain
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
