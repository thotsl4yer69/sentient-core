"""
Wake Word Detection Service for Sentient Core v2

Uses OpenWakeWord to detect wake words with sub-500ms latency.
Threaded PyAudio capture feeds a bounded queue for detection processing.

Architecture:
- Extends SentientService for MQTT lifecycle and config
- Audio capture runs in a daemon thread (PyAudio is synchronous)
- Detection loop runs async, consuming the freshest queued audio
- Publishes WAKE_WORD_DETECTED on successful detection with cooldown
"""

import asyncio
import json
import time
import threading
import queue
from pathlib import Path
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
MAX_QUEUED_CHUNKS = 5     # At most 400ms of waiting audio, excluding in-flight inference
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
        self.active_model_key: Optional[str] = None
        self._speech_active = False
        self._turn_busy = False
        self._gate_updated = time.monotonic()
        self._suspended_until = 0.0

        @self.on_mqtt(mqtt_topics.TTS_STARTED)
        async def tts_started(topic: str, payload: bytes):
            self._speech_active = True
            self._gate_updated = time.monotonic()

        @self.on_mqtt(mqtt_topics.TTS_COMPLETED)
        async def tts_completed(topic: str, payload: bytes):
            self._speech_active = False
            self._gate_updated = time.monotonic()
            self._suspended_until = self._gate_updated + 0.3

        @self.on_mqtt(mqtt_topics.AVATAR_STATE)
        async def avatar_state(topic: str, payload: bytes):
            try:
                state = json.loads(payload.decode("utf-8")).get("state")
            except (ValueError, AttributeError):
                return
            if state in {"alert", "listening", "processing", "thinking", "responding", "speaking"}:
                self._turn_busy = True
                self._gate_updated = time.monotonic()
            elif state == "idle":
                self._turn_busy = False
                self._gate_updated = time.monotonic()
                self._suspended_until = self._gate_updated + 0.3

    def _should_suspend(self) -> bool:
        now = time.monotonic()
        # Recover if a downstream service fails without sending an idle/end event.
        if self._speech_active or self._turn_busy:
            if now - self._gate_updated < 90.0:
                return True
            self._speech_active = False
            self._turn_busy = False
            self._suspended_until = now + 0.3
            self.logger.warning("Wake detection gate expired; re-arming after silence")
        return now < self._suspended_until

        # Threading for audio capture
        self.audio_thread: Optional[threading.Thread] = None
        self.audio_queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUED_CHUNKS)

        # Detection state
        self.last_detection_time: float = float("-inf")

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
        """Load and verify exactly the requested wake model.

        A phrase in the config is not a trained classifier. Built-in model
        names must match an installed OpenWakeWord model; custom phrases
        require an existing .onnx or .tflite model file.
        """
        try:
            selected = str(self.wake_model_name).strip()
            if not selected:
                raise ValueError("Wake-word model must be configured")

            custom_file = Path(selected).expanduser() if selected.lower().endswith(
                (".onnx", ".tflite")
            ) else None
            if custom_file is not None:
                if not custom_file.is_file():
                    raise FileNotFoundError(f"Custom wake model not found: {custom_file}")
                kwargs = {
                    "wakeword_models": [str(custom_file)],
                    "enable_speex_noise_suppression": False,
                }
                if custom_file.suffix.lower() == ".onnx":
                    kwargs["inference_framework"] = "onnx"
                self.oww_model = Model(**kwargs)
                expected = custom_file.stem.lower().replace(" ", "_")
            else:
                self.oww_model = Model(enable_speex_noise_suppression=False)
                expected = selected.lower().replace(" ", "_").replace("-", "_")

            available = list(self.oww_model.models.keys())
            matching = [
                name for name in available
                if name.lower().replace(" ", "_").replace("-", "_") == expected
                or name.lower().replace(" ", "_").replace("-", "_").startswith(expected + "_v")
            ]
            if len(matching) != 1:
                raise ValueError(
                    f"Configured wake model {selected!r} matched {matching}; "
                    f"available models: {available}. Set SENTIENT_WAKE_WORD_MODEL "
                    "to an installed model name or trained model file."
                )

            self.active_model_key = matching[0]
            self.logger.info(
                "Active wake word model: %s (threshold %.2f)",
                self.active_model_key, self.detection_threshold
            )
            return True

        except Exception as e:
            self.active_model_key = None
            self.logger.error(f"Failed to initialize requested wake word: {e}")
            return False

    def _audio_capture_thread(self):
        """Blocking audio capture running in a daemon thread.

        Reads chunks from PyAudio and pushes numpy arrays into the
        bounded thread-safe queue. Drop the oldest waiting chunk if inference
        falls behind; never accumulate pending asyncio put coroutines.
        """
        self.logger.info("Audio capture thread started")

        try:
            while self._running:
                try:
                    audio_data = self.stream.read(
                        CHUNK_SIZE, exception_on_overflow=False
                    )
                    audio_array = np.frombuffer(audio_data, dtype=np.int16)

                    try:
                        self.audio_queue.put_nowait(audio_array)
                    except queue.Full:
                        try:
                            self.audio_queue.get_nowait()
                        except queue.Empty:
                            pass  # The detection loop consumed it first.
                        self.audio_queue.put_nowait(audio_array)
                except Exception as e:
                    if self._running:
                        self.logger.error(f"Audio capture error: {e}")
                        time.sleep(0.1)  # Avoid a busy loop on a disconnected mic.
        except Exception as e:
            self.logger.error(f"Audio thread fatal error: {e}")
        finally:
            self.logger.info("Audio capture thread stopped")

    async def _detection_loop(self):
        """Main detection loop - processes audio from the queue."""
        self.logger.info("Detection loop started")

        was_suspended = False
        try:
            while self._running:
                try:
                    audio_array = await asyncio.to_thread(
                        self.audio_queue.get, True, 1.0
                    )

                    if self._should_suspend():
                        was_suspended = True
                        continue
                    if was_suspended:
                        # Clear the classifier's audio history so the assistant's
                        # last spoken phrase cannot activate it after playback.
                        reset = getattr(self.oww_model, "reset", None)
                        if callable(reset):
                            reset()
                        was_suspended = False

                    prediction = self.oww_model.predict(audio_array)

                    for model_name, score in prediction.items():
                        if model_name != self.active_model_key:
                            continue
                        if score >= self.detection_threshold:
                            self.logger.debug(
                                f"Model '{model_name}' triggered: {score:.3f}"
                            )
                            await self._publish_detection(score)
                            break  # Only publish once per chunk

                except queue.Empty:
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
        current_time = time.monotonic()

        if current_time - self.last_detection_time < self.cooldown_seconds:
            self.logger.debug(
                f"Detection ignored (cooldown): {confidence:.3f}"
            )
            return

        self.last_detection_time = current_time
        self._turn_busy = True
        self._gate_updated = current_time

        payload = {
            "timestamp": time.time(),
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
