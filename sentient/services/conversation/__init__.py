"""Conversation services.

Lazy import lets small deterministic skills be used and unit-tested without
loading the full MQTT, Redis and HTTP orchestration stack.
"""

__all__ = ["ConversationOrchestrator"]


def __getattr__(name):
    if name == "ConversationOrchestrator":
        from .orchestrator import ConversationOrchestrator
        return ConversationOrchestrator
    raise AttributeError(name)
