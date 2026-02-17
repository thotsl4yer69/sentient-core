"""Entry point for: python3 -m sentient.services.voice

Runs the wake word detector by default.
Use python3 -m sentient.services.voice.pipeline for the full voice pipeline.
"""
import asyncio
from sentient.services.voice.wake_word import WakeWordService

service = WakeWordService()
asyncio.run(service.run())
