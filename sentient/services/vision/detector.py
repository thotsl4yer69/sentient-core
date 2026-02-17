"""
Vision Service - Sentient Core v2

Placeholder vision detection service. In v1, vision processing was embedded
inside the perception layer. This service provides the structural skeleton
for a standalone vision pipeline (camera capture, YOLO inference, detection
publishing) that will be fleshed out when hardware is integrated.

Publishes:
- sentient/vision/detection   Detection results (objects, persons, etc.)

Subscribes:
- sentient/vision/detection   (loopback for logging / future chaining)
"""

import asyncio
import json
import time
from typing import Dict, Any, List, Optional

from sentient.config import get_config
from sentient.common.service_base import SentientService
from sentient.common import mqtt_topics


class VisionService(SentientService):
    """
    Placeholder vision detection service.

    Extends SentientService for MQTT lifecycle, config, and structured logging.
    Ready for future camera / YOLO integration.
    """

    def __init__(self):
        super().__init__(name="vision")

        # Placeholder state
        self._last_detection_time: float = 0.0
        self._detection_count: int = 0

        # Register MQTT handler for inbound detections (future chaining)
        @self.on_mqtt(mqtt_topics.VISION_DETECTION)
        async def _handle_detection(topic: str, payload: bytes):
            await self._on_vision_detection(topic, payload)

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def setup(self):
        """Initialise the vision service."""
        self.logger.info("Vision service ready (placeholder mode)")
        self.logger.info(
            "Camera / YOLO integration pending hardware availability"
        )

    async def teardown(self):
        """Clean up vision service resources."""
        self.logger.info(
            f"Vision service shutting down "
            f"(processed {self._detection_count} detections)"
        )

    # -------------------------------------------------------------------
    # MQTT handlers
    # -------------------------------------------------------------------

    async def _on_vision_detection(self, topic: str, payload: bytes):
        """Handle an inbound vision detection message."""
        try:
            data = json.loads(payload.decode())
            self._detection_count += 1
            self._last_detection_time = time.time()

            classes = data.get("classes", [])
            confidence = data.get("confidence", 0.0)
            source = data.get("source", "unknown")

            self.logger.info(
                f"Vision detection #{self._detection_count} from {source}: "
                f"{classes} (confidence: {confidence:.2f})"
            )
        except Exception as e:
            self.logger.error(f"Error handling vision detection: {e}")

    # -------------------------------------------------------------------
    # Publishing (for future use by camera / inference pipeline)
    # -------------------------------------------------------------------

    async def publish_detection(
        self,
        classes: List[str],
        confidence: float,
        source: str = "camera_0",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Publish a vision detection result.

        This method will be called by the camera/inference pipeline once
        integrated. For now it serves as the public API contract.
        """
        payload = {
            "classes": classes,
            "confidence": confidence,
            "source": source,
            "timestamp": time.time(),
        }
        if metadata:
            payload["metadata"] = metadata

        await self.mqtt_publish(mqtt_topics.VISION_DETECTION, payload)
        self.logger.info(
            f"Published detection: {classes} "
            f"(confidence: {confidence:.2f}, source: {source})"
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    service = VisionService()
    try:
        asyncio.run(service.run())
    except KeyboardInterrupt:
        service.logger.info("Service terminated by user")
    except Exception as e:
        service.logger.error(f"Service crashed: {e}", exc_info=True)
        sys.exit(1)
