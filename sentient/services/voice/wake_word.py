"""
Wake Word Detection Service for Sentient Core v2

Uses OpenWakeWord to detect wake words with sub-500ms latency.
Threaded PyAudio capture feeds an asyncio queue for detection processing.

Architecture:
- Extends SentientService for MQTT lifecycle and config
- Audio capture runs in a daemon thread (PyAudio is synchronous)
- Detection loop runs async, consuming from the audio queue
- Publishes WAKE_WORD_DETECTED on successful detection with cooldown
"""

import asyncio
import time
import threading
from typing import Optional

import numpy as np
import pyaudio
from openwakeword.model import Model

from sentient.config import get_config
from sentient.common.service_base import SentientService
from sentient.common import mqtt_topics

# Audio constants
SAMPLE_RATE = 16000       # OpenWakeWord expects 16kHz
CHUNK_SIZE = 1280         # 80ms chunks for low latency (16000 * 0.08)
CHANNELS = 1
FORMAT = pyaudio.paInt16

# Default cooldown between detections (seconds)
DEFAULT_COOLDOWN_SECONDS = 2.0


class WakeWordService(SentientService):
    """Wake word detection service using OpenWakeWord.

    Captures audio in a background thread, feeds chunks through an asyncio
    queue to the detection loop, and publishes detections via MQTT.
    """

    def __init__(self, cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS):
        super().__init__(name="wake_word")

        self.cfg = get_config()

        # Detection parameters from config
        self.detection_threshold: float = self.cfg.wake_word.sensitivity
        self.wake_model_name: str = self.cfg.wake_word.model
        self.cooldown_seconds: float = cooldown_seconds

        # Audio components
        self.audio: Optional[pyaudio.PyAudio] = None
        self.stream: Optional[pyaudio.Stream] = None
        self.oww_model: Optional[Model] = None

        # Threading for audio capture
        self.audio_thread: Optional[threading.Thread] = None
        self.audio_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Detection state
        self.last_detection_time: float = 0

    def _initialize_audio(self) -> bool:
        """Initialize PyAudio stream for microphone capture."""
        try:
            self.audio = pyaudio.PyAudio()

            default_input = self.audio.get_default_input_device_info()
            self.logger.info(f"Using audio input: {default_input['name']}")

            self.stream = self.audio.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
                stream_callback=None,  # Blocking mode for better control
            )

            self.logger.info(
                f"Audio stream initialized: {SAMPLE_RATE}Hz, "
                f"{CHUNK_SIZE} chunk size"
            )
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize audio: {e}")
            return False

    def _initialize_model(self) -> bool:
        """Initialize OpenWakeWord model."""
        try:
            self.oww_model = Model(
                wakeword_model_paths=[],
                enable_speex_noise_suppression=False,
            )

            available = list(self.oww_model.models.keys())
            self.logger.info(f"OpenWakeWord model initialized")
            self.logger.info(f"Available models: {available}")

            if not available:
                self.logger.warning(
                    "No models loaded! Service may not detect wake words."
                )
            else:
                self.logger.info(f"Loaded {len(available)} wake word models")

            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize OpenWakeWord model: {e}")
            return False

    def _audio_capture_thread(self):
        """Blocking audio capture running in a daemon thread.

        Reads chunks from PyAudio and pushes numpy arrays into the
        asyncio queue via run_coroutine_threadsafe.
        """
        self.logger.info("Audio capture thread started")

        try:
            while self._running:
                try:
                    audio_data = self.stream.read(
                        CHUNK_SIZE, exception_on_overflow=False
                    )
                    audio_array = np.frombuffer(audio_data, dtype=np.int16)

                    if self._loop:
                        asyncio.run_coroutine_threadsafe(
                            self.audio_queue.put(audio_array),
                            self._loop,
                        )
                except Exception as e:
                    if self._running:
                        self.logger.error(f"Audio capture error: {e}")
        except Exception as e:
            self.logger.error(f"Audio thread fatal error: {e}")
        finally:
            self.logger.info("Audio capture thread stopped")

    async def _detection_loop(self):
        """Main detection loop - processes audio from the queue."""
        self.logger.info("Detection loop started")

        try:
            while self._running:
                try:
                    audio_array = await asyncio.wait_for(
                        self.audio_queue.get(), timeout=1.0
                    )

                    prediction = self.oww_model.predict(audio_array)

                    for model_name, score in prediction.items():
                        if score >= self.detection_threshold:
                            self.logger.debug(
                                f"Model '{model_name}' triggered: {score:.3f}"
                            )
                            await self._publish_detection(score)
                            break  # Only publish once per chunk

                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    self.logger.error(f"Detection error: {e}")
                    await asyncio.sleep(0.1)
        except Exception as e:
            self.logger.error(f"Detection loop fatal error: {e}")
        finally:
            self.logger.info("Detection loop stopped")

    async def _publish_detection(self, confidence: float):
        """Publish a wake word detection event via MQTT with cooldown."""
        current_time = time.time()

        if current_time - self.last_detection_time < self.cooldown_seconds:
            self.logger.debug(
                f"Detection ignored (cooldown): {confidence:.3f}"
            )
            return

        self.last_detection_time = current_time

        payload = {
            "timestamp": current_time,
            "confidence": confidence,
            "service": "wake_word",
        }

        await self.mqtt_publish(mqtt_topics.WAKE_WORD_DETECTED, payload)
        self.logger.info(f"Wake word detected! Confidence: {confidence:.3f}")

    # --- SentientService lifecycle overrides ---

    async def setup(self):
        """Initialize audio hardware and OpenWakeWord model."""
        self.logger.info("Initializing wake word detection service...")

        if not self._initialize_audio():
            raise RuntimeError("Audio initialization failed")

        if not self._initialize_model():
            raise RuntimeError("Model initialization failed")

        self._loop = asyncio.get_running_loop()

        # Start audio capture in a daemon thread
        self.audio_thread = threading.Thread(
            target=self._audio_capture_thread,
            daemon=True,
        )
        self.audio_thread.start()

        # Start the detection loop as a background task
        self._tasks.append(
            asyncio.create_task(self._detection_loop())
        )

        self.logger.info(
            f"Wake word detection active - threshold: "
            f"{self.detection_threshold}, cooldown: {self.cooldown_seconds}s"
        )

    async def teardown(self):
        """Release audio resources."""
        self.logger.info("Stopping wake word detection service...")

        # Wait for audio thread to finish
        if self.audio_thread and self.audio_thread.is_alive():
            self.audio_thread.join(timeout=2.0)

        # Close audio stream
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass

        if self.audio:
            try:
                self.audio.terminate()
            except Exception:
                pass

        self.logger.info("Wake word detection service stopped")


if __name__ == "__main__":
    import sys

    service = WakeWordService()

    try:
        asyncio.run(service.run())
    except KeyboardInterrupt:
        service.logger.info("Service terminated by user")
    except Exception as e:
        service.logger.error(f"Service crashed: {e}", exc_info=True)
        sys.exit(1)
