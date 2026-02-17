"""Entry point for: python3 -m sentient.services.notifications"""
import asyncio
from sentient.services.notifications.service import NotificationServiceWrapper


if __name__ == "__main__":
    service = NotificationServiceWrapper()
    asyncio.run(service.run())
