"""Entry point for: python3 -m sentient.services.conversation"""
import asyncio
from sentient.services.conversation.orchestrator import ConversationOrchestrator

orchestrator = ConversationOrchestrator()
asyncio.run(orchestrator.run())
