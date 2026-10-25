"""
Voice Pipeline Service for Sentient Core v2

Complete wake word -> STT -> conversation -> TTS pipeline with visual feedback.

Flow:
1. Subscribe to sentient/wake/detected
2. On wake: alert avatar, start recording with VAD
3. Record audio until silence or max duration
4. Send to Whisper STT service
5. Get transcription -> send to conversation service
6. Get response -> TTS speaks it
7. Visual feedback on avatar during all states

Interrupt handling:
- Detect wake word while Cortana is speaking
- Stop TTS playback
- Process new input

Architecture:
- Extends SentientService for MQTT lifecycle and config
- Uses @self.on_mqtt() decorator for all subscriptions
- VAD via webrtcvad for silence detection
- PyAudio for audio capture
- All config via get_config(), all topics from mqtt_topics
"""

import asyncio
import base64
import json
import os
import tempfile
import time
import uuid
import wave
from datetime import datetime
from typing import Optional

import numpy as np
import pyaudio
import webrtcvad

from sentient.config import get_config
from sentient.common.service_base import SentientService
from sentient.common import mqtt_topics

# Audio constants
SAMPLE_RATE = 16000          # Standard for Whisper and VAD
CHANNELS = 1
FORMAT = pyaudio.paInt16
CHUNK_DURATION_MS = 30       # VAD operates on 10, 20, or 30ms frames
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION_MS / 1000)  # 480 samples

# Recording constants
MAX_RECORDING_SECONDS = 10   # Maximum recording duration
SILENCE_THRESHOLD_MS = 1500  # Stop after 1.5s of silence
VAD_AGGRESSIVENESS = 3       # 0-3, higher = more aggressive filtering
MIN_CHUNKS_FOR_VALID = 5     # At least 150ms of audio


class VoicePipeline(SentientService):
    """Full voice interaction pipeline: wake -> record -> STT -> chat -> TTS.

    Manages the complete voice interaction lifecycle with avatar state
    feedback and interrupt handling.
    """

    def __init__(self):
        super().__init__(name="voice_pipeline")

        self.cfg = get_config()

        # Audio components
        self.audio: Optional[pyaudio.PyAudio] = None
        self.stream: Optional[pyaudio.Stream] = None
        self.vad: Optional[webrtcvad.Vad] = None

        # Pipeline state
        self.is_recording: bool = False
        self.is_cortana_speaking: bool = False
        self.current_tts_id: Optional[str] = None
        self._turn_active: bool = False
        self._turn_started_monotonic: float = 0.0
        self._turn_id: Optional[str] = None
        self._turn_timeout_task: Optional[asyncio.Task] = None
        self._turn_timeout_seconds = max(
            15.0, float(os.environ.get("SENTIENT_VOICE_TURN_TIMEOUT", "190"))
        )

        # Audio buffer for current recording
        self._audio_buffer: list = []

        # Register all MQTT handlers
        @self.on_mqtt(mqtt_topics.WAKE_WORD_DETECTED)
        async def handle_wake_detected(topic: str, payload: bytes):
            try:
                data = json.loads(payload.decode("utf-8"))
                await self._handle_wake_detected(data)
            except Exception as e:
                self.logger.error(
                    f"Error handling wake word: {e}", exc_info=True
                )

        @self.on_mqtt(mqtt_topics.STT_OUTPUT)
        async def handle_stt_output(topic: str, payload: bytes):
            try:
                data = json.loads(payload.decode("utf-8"))
                await self._handle_stt_output(data)
            except Exception as e:
                self.logger.error(
                    f"Error handling STT output: {e}", exc_info=True
                )

        @self.on_mqtt(mqtt_topics.CHAT_OUTPUT)
        async def handle_chat_output(topic: str, payload: bytes):
            try:
                data = json.loads(payload.decode("utf-8"))
                await self._handle_persona_response(data)
            except Exception as exc:
                self.logger.error("Chat output handling failed: %s", exc, exc_info=True)

        @self.on_mqtt(mqtt_topics.PERSONA_RESPONSE)
        async def handle_persona_response(topic: str, payload: bytes):
            try:
                data = json.loads(payload.decode("utf-8"))
                await self._handle_persona_response(data)
            except Exception as e:
                self.logger.error(
                    f"Error handling persona response: {e}", exc_info=True
                )

        @self.on_mqtt(mqtt_topics.TTS_STARTED)
        async def handle_tts_started(topic: str, payload: bytes):
            try:
                data = json.loads(payload.decode("utf-8"))
                await self._handle_tts_started(data)
            except Exception as e:
                self.logger.error(
                    f"Error handling TTS started: {e}", exc_info=True
                )

        @self.on_mqtt(mqtt_topics.TTS_COMPLETED)
        async def handle_tts_completed(topic: str, payload: bytes):
            try:
                data = json.loads(payload.decode("utf-8"))
                await self._handle_tts_completed(data)
            except Exception as e:
                self.logger.error(
                    f"Error handling TTS completed: {e}", exc_info=True
                )

    async def _turn_watchdog(self, turn_id: str):
        """Rearm even if a downstream component never publishes completion."""
        try:
            await asyncio.sleep(self._turn_timeout_seconds)
            if self._turn_active and self._turn_id == turn_id:
                self.logger.error("Voice turn %s timed out; rearming", turn_id)
                await self._finish_turn(turn_id, reason="timeout")
        except asyncio.CancelledError:
            pass

    async def _finish_turn(self, turn_id: Optional[str], reason="completed"):
        if turn_id is not None and self._turn_id != turn_id:
            return
        if self._turn_timeout_task is not None:
            task = self._turn_timeout_task
            self._turn_timeout_task = None
            if task is not asyncio.current_task():
                task.cancel()
        self._turn_active = False
        self._turn_id = None
        self.is_cortana_speaking = False
        self.current_tts_id = None
        self.logger.info("Voice turn ended: %s", reason)
        await self._set_avatar_state("idle", {"reason": reason})

    # --- Avatar state management ---

    async def _set_avatar_state(self, state: str, metadata: Optional[dict] = None):
        """Publish avatar visual state via MQTT."""
        payload = {
            "state": state,
            "timestamp": datetime.now().isoformat(),
        }
        if metadata:
            payload.update(metadata)

        await self.mqtt_publish(mqtt_topics.AVATAR_STATE, payload)

    # --- Audio recording ---

    async def _record_audio_with_vad(self) -> Optional[bytes]:
        """Record audio until silence detected or max duration reached.

        Uses webrtcvad for voice activity detection. Returns WAV file
        bytes or None if the recording is too short or cancelled.
        """
        self.is_recording = True
        self._audio_buffer = []

        self.logger.info("Starting audio recording with VAD...")
        await self._set_avatar_state("listening")

        start_time = time.monotonic()
        consecutive_silence_ms = 0
        speech_seen = False

        try:
            while self.is_recording:
                # Check max duration
                if time.monotonic() - start_time > MAX_RECORDING_SECONDS:
                    self.logger.info("Max recording duration reached")
                    break

                # Read audio chunk
                try:
                    # PyAudio's blocking read must not stall the MQTT event loop.
                    audio_chunk = await asyncio.to_thread(
                        self.stream.read, CHUNK_SIZE, exception_on_overflow=False
                    )
                except Exception as e:
                    self.logger.error(f"Audio read error: {e}")
                    break

                self._audio_buffer.append(audio_chunk)

                # VAD check
                try:
                    is_speech = self.vad.is_speech(audio_chunk, SAMPLE_RATE)

                    if is_speech:
                        speech_seen = True
                        consecutive_silence_ms = 0
                    else:
                        consecutive_silence_ms += CHUNK_DURATION_MS

                        # Enough silence after some speech?
                        if (
                            speech_seen
                            and consecutive_silence_ms >= SILENCE_THRESHOLD_MS
                        ):
                            self.logger.info(
                                f"Silence detected ({consecutive_silence_ms}ms), "
                                f"stopping recording"
                            )
                            break

                except Exception as e:
                    self.logger.debug(f"VAD processing error: {e}")

                # Yield to event loop
                await asyncio.sleep(0.001)

            # Validate minimum length
            if not speech_seen or len(self._audio_buffer) < MIN_CHUNKS_FOR_VALID:
                self.logger.warning("Recording too short, discarding")
                await self._set_avatar_state("idle")
                return None

            wav_bytes = self._create_wav_bytes(self._audio_buffer)

            duration = len(self._audio_buffer) * CHUNK_DURATION_MS / 1000.0
            self.logger.info(
                f"Recording complete: {duration:.2f}s, {len(wav_bytes)} bytes"
            )
            return wav_bytes

        except Exception as e:
            self.logger.error(f"Recording error: {e}")
            await self._set_avatar_state(
                "error", {"message": "Recording failed"}
            )
            return None
        finally:
            self.is_recording = False
            self._audio_buffer = []

    def _create_wav_bytes(self, audio_chunks: list) -> bytes:
        """Create WAV file bytes from raw audio chunks."""
        wav_path = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False
            ) as tmp:
                wav_path = tmp.name

            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(self.audio.get_sample_size(FORMAT))
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(b"".join(audio_chunks))

            with open(wav_path, "rb") as f:
                return f.read()
        finally:
            if wav_path:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass

    # --- Pipeline stage methods ---

    async def _send_to_stt(self, audio_bytes: bytes):
        """Send recorded audio to the Whisper STT service."""
        self.logger.info("Sending audio to STT service...")
        await self._set_avatar_state("processing", {"stage": "transcribing"})

        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

        payload = {
            "audio": {
                "data": audio_base64,
                "format": "wav",
                "sample_rate": SAMPLE_RATE,
                "channels": CHANNELS,
            },
            "source": "voice_pipeline",
            "turn_id": self._turn_id,
            "timestamp": datetime.now().isoformat(),
        }

        await self.mqtt_publish(mqtt_topics.STT_AUDIO_INPUT, payload)
        self.logger.info("Audio sent to STT service")

    async def _send_to_conversation(self, text: str):
        """Send transcribed text to the persona/conversation service."""
        self.logger.info(f"Sending to conversation: '{text}'")
        await self._set_avatar_state("thinking", {"input": text})

        payload = {
            "text": text,
            "source": "voice_pipeline",
            "user": "voice_user",
            "confidence": 1.0,
            "turn_id": self._turn_id,
            "timestamp": datetime.now().isoformat(),
        }

        # This is the topic consumed by ConversationOrchestrator. Publishing to
        # PERSONA_CHAT_INPUT silently drops speech in the maintained v2 stack.
        await self.mqtt_publish(mqtt_topics.VOICE_TRANSCRIPTION, payload)
        self.logger.info("Sent to conversation service")

    # --- MQTT event handlers ---

    async def _handle_wake_detected(self, payload: dict):
        """Handle wake word detection - start the voice pipeline."""
        confidence = payload.get("confidence", 0.0)
        self.logger.info(
            f"Wake word detected (confidence: {confidence:.3f})"
        )

        # Ignore repeated wakes until the current turn completes. Recover if
        # the downstream service never emits a terminal TTS event.
        if self._turn_active and time.monotonic() - self._turn_started_monotonic < 90.0:
            self.logger.debug("Ignoring duplicate wake while turn is active")
            return
        self._turn_active = True
        self._turn_started_monotonic = time.monotonic()
        self._turn_id = uuid.uuid4().hex
        if self._turn_timeout_task:
            self._turn_timeout_task.cancel()
        self._turn_timeout_task = asyncio.create_task(
            self._turn_watchdog(self._turn_id)
        )

        # Interrupt current TTS playback if speaking
        if self.is_cortana_speaking:
            self.logger.info("Interrupting current TTS playback")
            await self.mqtt_publish(
                mqtt_topics.TTS_STOP,
                {
                    "reason": "wake_word_interrupt",
                    "timestamp": datetime.now().isoformat(),
                },
            )
            self.is_cortana_speaking = False

        # Alert avatar
        await self._set_avatar_state(
            "alert", {"trigger": "wake_word", "confidence": confidence}
        )

        # Start listening immediately: delaying for avatar animation clips
        # the first spoken word after the wake phrase.
        # Record audio with VAD
        audio_bytes = await self._record_audio_with_vad()

        if audio_bytes:
            await self._send_to_stt(audio_bytes)
        else:
            self.logger.warning("No audio captured after wake word")
            await self._finish_turn(self._turn_id, "no_audio")

    async def _handle_stt_output(self, payload: dict):
        """Handle STT transcription result only for the active voice turn."""
        if not self._turn_active:
            self.logger.debug("Ignoring STT output with no active voice turn")
            return
        if (payload.get("turn_id") and self._turn_id
                and payload["turn_id"] != self._turn_id):
            self.logger.warning("Ignoring late STT output for different voice turn")
            return
        text = payload.get("text", "").strip()
        language = payload.get("language", "unknown")
        confidence = payload.get("language_probability", 0.0)

        if not text:
            self.logger.warning("Empty transcription received")
            await self._finish_turn(self._turn_id, "empty_stt")
            return

        self.logger.info(
            f"Transcription: '{text}' "
            f"(lang: {language}, conf: {confidence:.3f})"
        )

        await self._send_to_conversation(text)

    async def _handle_persona_response(self, payload: dict):
        """Handle Cortana's response from the persona service."""
        if (payload.get("turn_id") and self._turn_id
                and payload["turn_id"] != self._turn_id):
            return
        # Ignore unrelated web chat replies while a voice turn is in flight.
        if not self._turn_active or payload.get("user", "voice_user") not in (
            "voice_user", "unknown"
        ):
            return
        text = payload.get("text", "").strip()
        emotion = payload.get("emotion", "neutral")

        if not text:
            self.logger.warning("Empty response from persona")
            await self._finish_turn(self._turn_id, "empty_response")
            return

        self.logger.info(
            f"Persona response: '{text[:50]}...' (emotion: {emotion})"
        )

        await self._set_avatar_state(
            "responding",
            {"emotion": emotion, "text_preview": text[:100]},
        )
        # Response is automatically routed to TTS by the persona service;
        # we wait for TTS_STARTED / TTS_COMPLETED events.

    async def _handle_tts_started(self, payload: dict):
        """Handle TTS playback started event."""
        if (payload.get("turn_id") and self._turn_id
                and payload["turn_id"] != self._turn_id):
            return
        tts_id = payload.get("id", "unknown")
        self.current_tts_id = tts_id
        self.is_cortana_speaking = True

        self.logger.info(f"TTS playback started: {tts_id}")
        await self._set_avatar_state("speaking", {"tts_id": tts_id})

    async def _handle_tts_completed(self, payload: dict):
        """Handle TTS playback completed event."""
        if (payload.get("turn_id") and self._turn_id
                and payload["turn_id"] != self._turn_id):
            return
        tts_id = payload.get("id", "unknown")
        self.logger.info(f"TTS playback completed: {tts_id}")

        if tts_id == self.current_tts_id:
            await self._finish_turn(self._turn_id, payload.get("status", "completed"))

    # --- SentientService lifecycle overrides ---

    async def setup(self):
        """Initialize VAD and PyAudio for voice recording."""
        self.logger.info("Initializing Voice Pipeline...")

        # Initialize VAD
        try:
            self.vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
            self.logger.info(
                f"VAD initialized (aggressiveness: {VAD_AGGRESSIVENESS})"
            )
        except Exception as e:
            self.logger.error(f"Failed to initialize VAD: {e}")
            raise

        # Initialize PyAudio
        try:
            self.audio = pyaudio.PyAudio()

            default_input = self.audio.get_default_input_device_info()
            self.logger.info(f"Audio input: {default_input['name']}")

            self.stream = self.audio.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )

            self.logger.info(
                f"Audio stream initialized: {SAMPLE_RATE}Hz, "
                f"chunk {CHUNK_SIZE}"
            )
        except Exception as e:
            self.logger.error(f"Failed to initialize audio: {e}")
            raise

        # Set initial avatar state
        await self._set_avatar_state(
            "idle", {"mode": "voice_pipeline", "ready": True}
        )

        self.logger.info("Voice Pipeline initialized - listening for wake word")

    async def teardown(self):
        """Release audio resources."""
        self.logger.info("Shutting down Voice Pipeline...")

        self.is_recording = False
        self._turn_active = False
        if self._turn_timeout_task:
            self._turn_timeout_task.cancel()
            self._turn_timeout_task = None

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

        self.logger.info("Voice Pipeline stopped")


if __name__ == "__main__":
    import sys

    pipeline = VoicePipeline()

    try:
        asyncio.run(pipeline.run())
    except KeyboardInterrupt:
        pipeline.logger.info("Service terminated by user")
    except Exception as e:
        pipeline.logger.error(f"Service crashed: {e}", exc_info=True)
        sys.exit(1)
