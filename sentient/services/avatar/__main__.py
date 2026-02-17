"""Entry point for: python3 -m sentient.services.avatar"""
import asyncio
from sentient.services.avatar.bridge import AvatarBridgeService

service = AvatarBridgeService()
asyncio.run(service.run())
