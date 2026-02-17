#!/usr/bin/env python3
"""
Wake Word Detection Service for Sentient Core
Uses OpenWakeWord to detect "Hey Cortana" with sub-500ms latency
"""

import asyncio
import logging
import signal
import sys
import threading
from pathlib import Path
from typing import Optional

import numpy as np
import pyaudio
from openwakeword.model import Model

try:
    from aiomqtt import Client as MQTTClient
except ImportError:
    import paho.mqtt.client as mqtt_legacy
    MQTTClient = None

# Configuration
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC_DETECTED = "sentient/wake/detected"
MQTT_TOPIC_AVATAR = "sentient/avatar/wake"

# Audio Configuration
SAMPLE_RATE = 16000  # OpenWakeWord expects 16kHz
CHUNK_SIZE = 1280    # 80ms chunks for low latency (16000 * 0.08)
CHANNELS = 1
FORMAT = pyaudio.paInt16

# Detection Configuration
DETECTION_THRESHOLD = 0.5  # Confidence threshold
COOLDOWN_SECONDS = 2.0     # Prevent rapid re-triggers

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/var/log/sentient/wake_word.log')
    ]
)
logger = logging.getLogger('WakeWord')


class WakeWordDetector:
    """Production-ready wake word detector with async MQTT publishing"""

    def __init__(self):
        self.running = False
        self.audio = None
        self.stream = None
        self.oww_model = None
        self.last_detection_time = 0
        self.mqtt_client = None
        self.loop = None

        # Threading for audio input (PyAudio is synchronous)
        self.audio_thread = None
        self.audio_queue = asyncio.Queue()

    def initialize_audio(self):
        """Initialize PyAudio stream"""
        try:
            self.audio = pyaudio.PyAudio()

            # Find default input device
            default_input = self.audio.get_default_input_device_info()
            logger.info(f"Using audio input: {default_input['name']}")

            self.stream = self.audio.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
                stream_callback=None  # Use blocking mode for better control
            )

            logger.info(f"Audio stream initialized: {SAMPLE_RATE}Hz, {CHUNK_SIZE} chunk size")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize audio: {e}")
            return False

    def initialize_model(self):
        """Initialize OpenWakeWord model"""
        try:
            # Initialize with pre-trained models
            # Empty list will auto-load all default pre-trained models
            self.oww_model = Model(
                wakeword_model_paths=[],  # Empty list auto-loads default models
                enable_speex_noise_suppression=False  # Disable to avoid dependency issues
            )

            logger.info("OpenWakeWord model initialized")
            logger.info(f"Available models: {list(self.oww_model.models.keys())}")

            # Verify we have models loaded
            available = list(self.oww_model.models.keys())
            if not available:
                logger.warning("No models loaded! Service may not detect wake words.")
            else:
                logger.info(f"Loaded {len(available)} wake word models")

            return True

        except Exception as e:
            logger.error(f"Failed to initialize OpenWakeWord model: {e}")
            return False

    async def publish_detection(self, confidence: float):
        """Publish wake word detection to MQTT"""
        try:
            import time
            current_time = time.time()

            # Cooldown check
            if current_time - self.last_detection_time < COOLDOWN_SECONDS:
                logger.debug(f"Detection ignored (cooldown): {confidence:.3f}")
                return

            self.last_detection_time = current_time

            payload = {
                "timestamp": current_time,
                "confidence": confidence,
                "service": "wake_word"
            }

            if MQTTClient:
                # Use aiomqtt
                async with MQTTClient(MQTT_BROKER, MQTT_PORT) as client:
                    await client.publish(MQTT_TOPIC_DETECTED, str(payload))
                    await client.publish(MQTT_TOPIC_AVATAR, "wake")
                    logger.info(f"Wake word detected! Confidence: {confidence:.3f}")
            else:
                # Fallback to paho-mqtt
                client = mqtt_legacy.Client()
                client.connect(MQTT_BROKER, MQTT_PORT)
                client.publish(MQTT_TOPIC_DETECTED, str(payload))
                client.publish(MQTT_TOPIC_AVATAR, "wake")
                client.disconnect()
                logger.info(f"Wake word detected! Confidence: {confidence:.3f}")

        except Exception as e:
            logger.error(f"Failed to publish detection: {e}")

    def audio_capture_thread(self):
        """Blocking audio capture in separate thread"""
        logger.info("Audio capture thread started")

        try:
            while self.running:
                try:
                    # Read audio chunk (blocking)
                    audio_data = self.stream.read(CHUNK_SIZE, exception_on_overflow=False)

                    # Convert to numpy array
                    audio_array = np.frombuffer(audio_data, dtype=np.int16)

                    # Put in async queue (non-blocking)
                    if self.loop:
                        asyncio.run_coroutine_threadsafe(
                            self.audio_queue.put(audio_array),
                            self.loop
                        )

                except Exception as e:
                    if self.running:
                        logger.error(f"Audio capture error: {e}")

        except Exception as e:
            logger.error(f"Audio thread fatal error: {e}")
        finally:
            logger.info("Audio capture thread stopped")

    async def detection_loop(self):
        """Main detection loop - processes audio from queue"""
        logger.info("Detection loop started")

        try:
            while self.running:
                try:
                    # Get audio chunk from queue (with timeout)
                    audio_array = await asyncio.wait_for(
                        self.audio_queue.get(),
                        timeout=1.0
                    )

                    # Run prediction
                    prediction = self.oww_model.predict(audio_array)

                    # Check all models for detection
                    for model_name, score in prediction.items():
                        if score >= DETECTION_THRESHOLD:
                            logger.debug(f"Model '{model_name}' triggered: {score:.3f}")
                            await self.publish_detection(score)
                            break  # Only publish once per chunk

                except asyncio.TimeoutError:
                    # No audio data available, continue
                    continue

                except Exception as e:
                    logger.error(f"Detection error: {e}")
                    await asyncio.sleep(0.1)

        except Exception as e:
            logger.error(f"Detection loop fatal error: {e}")
        finally:
            logger.info("Detection loop stopped")

    async def start(self):
        """Start the wake word detection service"""
        logger.info("Starting wake word detection service...")

        # Initialize components
        if not self.initialize_audio():
            logger.error("Audio initialization failed")
            return False

        if not self.initialize_model():
            logger.error("Model initialization failed")
            return False

        # Create log directory if needed
        Path('/var/log/sentient').mkdir(parents=True, exist_ok=True)

        self.running = True
        self.loop = asyncio.get_running_loop()

        # Start audio capture thread
        self.audio_thread = threading.Thread(
            target=self.audio_capture_thread,
            daemon=True
        )
        self.audio_thread.start()

        # Run detection loop
        logger.info("Wake word detection active - listening for 'Hey Cortana'...")
        await self.detection_loop()

        return True

    async def stop(self):
        """Gracefully stop the service"""
        logger.info("Stopping wake word detection service...")

        self.running = False

        # Wait for audio thread to finish
        if self.audio_thread and self.audio_thread.is_alive():
            self.audio_thread.join(timeout=2.0)

        # Close audio stream
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()

        if self.audio:
            self.audio.terminate()

        logger.info("Wake word detection service stopped")


# Global detector instance
detector = None


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}, shutting down...")
    if detector:
        asyncio.create_task(detector.stop())
    sys.exit(0)


async def main():
    """Main entry point"""
    global detector

    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Create and start detector
    detector = WakeWordDetector()

    try:
        await detector.start()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        await detector.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Service terminated by user")
    except Exception as e:
        logger.error(f"Service crashed: {e}", exc_info=True)
        sys.exit(1)
