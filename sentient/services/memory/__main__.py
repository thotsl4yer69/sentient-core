"""Entry point for: python3 -m sentient.services.memory"""
import asyncio
from sentient.services.memory.api import MemoryService

service = MemoryService()
asyncio.run(service.run())
