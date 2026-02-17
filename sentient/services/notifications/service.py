#!/usr/bin/env python3
"""
Notification Service - MQTT-based notification dispatcher for Sentient Core.
Listens to sentient/notifications/send and forwards to ntfy.sh.
"""

import asyncio
import json
from typing import Optional

from sentient.common.service_base import SentientService
from sentient.common.mqtt_topics import NOTIFICATION_SEND
from sentient.services.notifications.engine import NotificationService


class NotificationServiceWrapper(SentientService):
    """
    MQTT-listening wrapper for NotificationService.
    Receives notification requests via MQTT and dispatches to ntfy.sh.
    """

    def __init__(self):
        """Initialize notification service wrapper"""
        super().__init__(name="notifications", http_port=None)

        # Notification engine instance
        self.notification_engine: Optional[NotificationService] = None

    async def setup(self):
        """Service-specific initialization"""
        # Initialize notification engine
        self.notification_engine = NotificationService()

        if not await self.notification_engine.initialize():
            self.logger.error("Failed to initialize notification engine")
            raise RuntimeError("Notification engine initialization failed")

        self.logger.info("✓ Notification engine initialized")

        # Subscribe to notification send topic
        self.on_mqtt(NOTIFICATION_SEND)(self._on_notification_request)
        self.logger.info(f"✓ Subscribed to {NOTIFICATION_SEND}")

    async def teardown(self):
        """Service-specific cleanup"""
        if self.notification_engine:
            await self.notification_engine.shutdown()
            self.logger.info("Notification engine shutdown complete")

    async def _on_notification_request(self, topic: str, payload: bytes):
        """
        Handle incoming notification requests from MQTT.

        Expected payload:
        {
            "title": "Alert Title",
            "message": "Alert message content",
            "priority": "INFO" | "ALERT" | "URGENT",
            "tags": ["tag1", "tag2"]
        }
        """
        try:
            data = json.loads(payload.decode())

            # Extract fields
            title = data.get("title", "Cortana Notification")
            message = data.get("message", "")
            priority = data.get("priority", "INFO").upper()
            tags = data.get("tags", [])

            # Validate message
            if not message or not message.strip():
                self.logger.warning("Received notification request with empty message")
                return

            # Send notification via engine
            success = await self.notification_engine.send_notification(
                message=message,
                priority=priority,
                title=title,
                tags=tags
            )

            if success:
                self.logger.info(f"Notification sent: {title} - {message[:50]}...")
            else:
                self.logger.warning(f"Failed to send notification: {title}")

        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in notification request: {e}")
        except Exception as e:
            self.logger.error(f"Error processing notification request: {e}", exc_info=True)


if __name__ == "__main__":
    service = NotificationServiceWrapper()
    asyncio.run(service.run())
