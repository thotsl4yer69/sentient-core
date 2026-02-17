"""Entry point for: python3 -m sentient.services.proactive"""
import asyncio
from sentient.services.proactive.engine import ProactiveBehaviorEngine

engine = ProactiveBehaviorEngine()
asyncio.run(engine.run())
