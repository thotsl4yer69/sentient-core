#!/usr/bin/env python3
"""
Voice-First Mode for Sentient Core
Complete wake word → STT → Contemplation → TTS pipeline with visual feedback

Flow:
1. Subscribe to sentient/wake/detected
2. On wake: Alert avatar, start recording
3. Record audio for 5 seconds or until silence (VAD)
4. Send to Whisper STT service
5. Get transcription
6. Send to conversation service
7. Get response
8. Send to Piper TTS service
9. Visual feedback on avatar during all states

Interrupt handling:
- Detect if Jack speaks while Cortana speaking
- Stop TTS playback
- Process new input
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional
import tempfile
import base64

import numpy as np
import pyaudio
import webrtcvad
from aiomqtt import Client as MQTTClient

# Configuration
MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "sentient")
MQTT_PASS = os.getenv("MQTT_PASS", "sentient1312")

# Audio Configuration
SAMPLE_RATE = 16000  # Standard for both Whisper and VAD
CHANNELS = 1
FORMAT = pyaudio.paInt16
CHUNK_DURATION_MS = 30  # VAD operates on 10, 20, or 30ms frames
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION_MS / 1000)  # 480 samples for 30ms at 16kHz

# Recording Configuration
MAX_RECORDING_SECONDS = 10  # Maximum recording duration
SILENCE_THRESHOLD_MS = 1500  # Stop after 1.5s of silence
VAD_AGGRESSIVENESS = 3  # 0-3, higher = more aggressive filtering

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/var/log/sentient/voice.log')
    ]
)
logger = logging.getLogger('VoiceMode')


class VoiceFirstMode:
    """Production-ready voice interaction system"""

    def __init__(self):
        self.running = False
        self.mqtt_client: Optional[MQTTClient] = None

        # Audio components
        self.audio = None
        self.stream = None
        self.vad = None

        # State management
        self.is_recording = False
        self.is_cortana_speaking = False
        self.current_tts_id = None

        # Audio buffer for recording
        self.audio_buffer = []

        # Create log directory
        Path('/var/log/sentient').mkdir(parents=True, exist_ok=True)

    async def initialize(self):
        """Initialize all components"""
        logger.info("Initializing Voice-First Mode...")

        # Initialize VAD
        try:
            self.vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
            logger.info(f"VAD initialized (aggressiveness: {VAD_AGGRESSIVENESS})")
        except Exception as e:
            logger.error(f"Failed to initialize VAD: {e}")
            raise

        # Initialize PyAudio
        try:
            self.audio = pyaudio.PyAudio()

            # Find default input device
            default_input = self.audio.get_default_input_device_info()
            logger.info(f"Audio input: {default_input['name']}")

            self.stream = self.audio.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE
            )

            logger.info(f"Audio stream initialized: {SAMPLE_RATE}Hz, chunk {CHUNK_SIZE}")
        except Exception as e:
            logger.error(f"Failed to initialize audio: {e}")
            raise

        # Connect to MQTT
        try:
            self.mqtt_client = MQTTClient(
                hostname=MQTT_BROKER,
                port=MQTT_PORT,
                username=MQTT_USER,
                password=MQTT_PASS
            )
            await self.mqtt_client.__aenter__()
            logger.info(f"Connected to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}")

            # Subscribe to topics
            await self.mqtt_client.subscribe("sentient/wake/detected")
            await self.mqtt_client.subscribe("sentient/stt/output")
            await self.mqtt_client.subscribe("sentient/persona/response")
            await self.mqtt_client.subscribe("sentient/tts/output")
            await self.mqtt_client.subscribe("sentient/tts/started")
            await self.mqtt_client.subscribe("sentient/tts/completed")

            logger.info("Subscribed to voice interaction topics")
        except Exception as e:
            logger.error(f"Failed to connect to MQTT: {e}")
            raise

        logger.info("Voice-First Mode initialized successfully")

    async def publish(self, topic: str, payload: dict):
        """Publish message to MQTT"""
        try:
            await self.mqtt_client.publish(topic, json.dumps(payload))
            logger.debug(f"Published to {topic}: {payload}")
        except Exception as e:
            logger.error(f"Failed to publish to {topic}: {e}")

    async def set_avatar_state(self, state: str, metadata: dict = None):
        """Set avatar visual state"""
        payload = {
            "state": state,
            "timestamp": datetime.now().isoformat()
        }
        if metadata:
            payload.update(metadata)

        await self.publish("sentient/avatar/state", payload)

    async def record_audio_with_vad(self) -> Optional[bytes]:
        """
        Record audio until silence detected or max duration reached
        Returns WAV file bytes or None if cancelled
        """
        self.is_recording = True
        self.audio_buffer = []

        logger.info("Starting audio recording with VAD...")

        # Set avatar to listening state
        await self.set_avatar_state("listening")

        start_time = time.time()
        last_speech_time = start_time
        consecutive_silence_ms = 0

        try:
            while self.is_recording:
                # Check max duration
                if time.time() - start_time > MAX_RECORDING_SECONDS:
                    logger.info("Max recording duration reached")
                    break

                # Read audio chunk
                try:
                    audio_chunk = self.stream.read(CHUNK_SIZE, exception_on_overflow=False)
                except Exception as e:
                    logger.error(f"Audio read error: {e}")
                    break

                # Add to buffer
                self.audio_buffer.append(audio_chunk)

                # VAD check (requires exactly CHUNK_SIZE bytes for 30ms frame)
                try:
                    is_speech = self.vad.is_speech(audio_chunk, SAMPLE_RATE)

                    if is_speech:
                        # Speech detected - reset silence counter
                        last_speech_time = time.time()
                        consecutive_silence_ms = 0
                    else:
                        # Silence detected - increment counter
                        consecutive_silence_ms += CHUNK_DURATION_MS

                        # Check if we've had enough silence after speech
                        if len(self.audio_buffer) > 10 and consecutive_silence_ms >= SILENCE_THRESHOLD_MS:
                            logger.info(f"Silence detected ({consecutive_silence_ms}ms), stopping recording")
                            break

                except Exception as e:
                    logger.debug(f"VAD processing error: {e}")
                    # Continue recording even if VAD fails

                # Small sleep to prevent CPU spinning
                await asyncio.sleep(0.001)

            # Check if we have enough audio
            if len(self.audio_buffer) < 5:  # At least 150ms of audio
                logger.warning("Recording too short, discarding")
                await self.set_avatar_state("idle")
                return None

            # Convert buffer to WAV bytes
            wav_bytes = self._create_wav_bytes(self.audio_buffer)

            duration = len(self.audio_buffer) * CHUNK_DURATION_MS / 1000.0
            logger.info(f"Recording complete: {duration:.2f}s, {len(wav_bytes)} bytes")

            return wav_bytes

        except Exception as e:
            logger.error(f"Recording error: {e}")
            await self.set_avatar_state("error", {"message": "Recording failed"})
            return None
        finally:
            self.is_recording = False
            self.audio_buffer = []

    def _create_wav_bytes(self, audio_chunks: list) -> bytes:
        """Create WAV file bytes from audio chunks"""
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as wav_file:
            wav_path = wav_file.name

        try:
            # Write WAV file
            with wave.open(wav_path, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(self.audio.get_sample_size(FORMAT))
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(b''.join(audio_chunks))

            # Read back as bytes
            with open(wav_path, 'rb') as f:
                wav_bytes = f.read()

            return wav_bytes

        finally:
            # Clean up temp file
            try:
                os.unlink(wav_path)
            except:
                pass

    async def send_to_stt(self, audio_bytes: bytes):
        """Send audio to Whisper STT service"""
        logger.info("Sending audio to STT service...")

        await self.set_avatar_state("processing", {"stage": "transcribing"})

        # Encode audio as base64
        audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')

        payload = {
            "audio": {
                "data": audio_base64,
                "format": "wav",
                "sample_rate": SAMPLE_RATE,
                "channels": CHANNELS
            },
            "source": "voice_mode",
            "timestamp": datetime.now().isoformat()
        }

        await self.publish("sentient/stt/audio/input", payload)
        logger.info("Audio sent to STT service")

    async def send_to_conversation(self, text: str):
        """Send transcribed text to conversation service"""
        logger.info(f"Sending to conversation: '{text}'")

        await self.set_avatar_state("thinking", {"input": text})

        payload = {
            "text": text,
            "source": "voice_mode",
            "timestamp": datetime.now().isoformat()
        }

        # Send to Cortana persona service
        await self.publish("sentient/persona/chat/input", payload)
        logger.info("Sent to conversation service")

    async def handle_wake_detected(self, payload: dict):
        """Handle wake word detection"""
        confidence = payload.get("confidence", 0.0)
        logger.info(f"Wake word detected (confidence: {confidence:.3f})")

        # Stop any current TTS playback
        if self.is_cortana_speaking:
            logger.info("Interrupting current TTS playback")
            await self.publish("sentient/tts/stop", {
                "reason": "wake_word_interrupt",
                "timestamp": datetime.now().isoformat()
            })
            self.is_cortana_speaking = False

        # Alert avatar
        await self.set_avatar_state("alert", {
            "trigger": "wake_word",
            "confidence": confidence
        })

        # Small delay for avatar animation
        await asyncio.sleep(0.3)

        # Start recording
        audio_bytes = await self.record_audio_with_vad()

        if audio_bytes:
            # Send to STT
            await self.send_to_stt(audio_bytes)
        else:
            # No audio captured, return to idle
            logger.warning("No audio captured after wake word")
            await self.set_avatar_state("idle")

    async def handle_stt_output(self, payload: dict):
        """Handle STT transcription result"""
        text = payload.get("text", "").strip()
        language = payload.get("language", "unknown")
        confidence = payload.get("language_probability", 0.0)

        if not text:
            logger.warning("Empty transcription received")
            await self.set_avatar_state("idle")
            return

        logger.info(f"Transcription: '{text}' (lang: {language}, conf: {confidence:.3f})")

        # Send to conversation
        await self.send_to_conversation(text)

    async def handle_persona_response(self, payload: dict):
        """Handle Cortana's response from persona service"""
        text = payload.get("text", "").strip()
        emotion = payload.get("emotion", "neutral")

        if not text:
            logger.warning("Empty response from persona")
            await self.set_avatar_state("idle")
            return

        logger.info(f"Persona response: '{text[:50]}...' (emotion: {emotion})")

        # Update avatar emotion
        await self.set_avatar_state("responding", {
            "emotion": emotion,
            "text_preview": text[:100]
        })

        # Response will be automatically sent to TTS by persona service
        # We just wait for TTS events

    async def handle_tts_started(self, payload: dict):
        """Handle TTS playback started"""
        tts_id = payload.get("id", "unknown")
        self.current_tts_id = tts_id
        self.is_cortana_speaking = True

        logger.info(f"TTS playback started: {tts_id}")

        await self.set_avatar_state("speaking", {
            "tts_id": tts_id
        })

    async def handle_tts_completed(self, payload: dict):
        """Handle TTS playback completed"""
        tts_id = payload.get("id", "unknown")

        logger.info(f"TTS playback completed: {tts_id}")

        if tts_id == self.current_tts_id:
            self.is_cortana_speaking = False
            self.current_tts_id = None

            # Return to idle
            await self.set_avatar_state("idle")

    async def handle_tts_output(self, payload: dict):
        """Handle TTS audio output (for monitoring)"""
        # This is primarily for logging/monitoring
        # Actual playback happens via WebSocket or other mechanisms
        text = payload.get("text", "")
        emotion = payload.get("emotion", "neutral")

        logger.debug(f"TTS output generated: '{text[:50]}...' (emotion: {emotion})")

    async def message_loop(self):
        """Main MQTT message processing loop"""
        logger.info("Starting message loop...")

        try:
            async for message in self.mqtt_client.messages:
                if not self.running:
                    break

                topic = message.topic.value

                # Parse payload
                try:
                    payload_str = message.payload.decode()

                    # Try to parse as JSON
                    try:
                        payload = json.loads(payload_str)
                    except json.JSONDecodeError:
                        # Plain string payload
                        payload = {"text": payload_str}

                    # Route to handlers
                    if topic == "sentient/wake/detected":
                        await self.handle_wake_detected(payload)

                    elif topic == "sentient/stt/output":
                        await self.handle_stt_output(payload)

                    elif topic == "sentient/persona/response":
                        await self.handle_persona_response(payload)

                    elif topic == "sentient/tts/output":
                        await self.handle_tts_output(payload)

                    elif topic == "sentient/tts/started":
                        await self.handle_tts_started(payload)

                    elif topic == "sentient/tts/completed":
                        await self.handle_tts_completed(payload)

                except Exception as e:
                    logger.error(f"Error processing message on {topic}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Message loop error: {e}", exc_info=True)
        finally:
            logger.info("Message loop stopped")

    async def run(self):
        """Main run loop"""
        logger.info("Starting Voice-First Mode...")

        try:
            # Initialize components
            await self.initialize()

            # Set initial state
            await self.set_avatar_state("idle", {
                "mode": "voice_first",
                "ready": True
            })

            self.running = True

            logger.info("Voice-First Mode active - listening for wake word")

            # Run message loop
            await self.message_loop()

        except Exception as e:
            logger.error(f"Fatal error in run loop: {e}", exc_info=True)
            raise
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Graceful shutdown"""
        logger.info("Shutting down Voice-First Mode...")

        self.running = False
        self.is_recording = False

        # Stop audio stream
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except:
                pass

        if self.audio:
            try:
                self.audio.terminate()
            except:
                pass

        # Disconnect MQTT
        if self.mqtt_client:
            try:
                await self.mqtt_client.__aexit__(None, None, None)
            except:
                pass

        logger.info("Voice-First Mode stopped")


# Global instance
voice_mode = None


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}, shutting down...")
    if voice_mode:
        voice_mode.running = False
    sys.exit(0)


async def main():
    """Main entry point"""
    global voice_mode

    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Create and run voice mode
    voice_mode = VoiceFirstMode()

    try:
        await voice_mode.run()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        await voice_mode.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Service terminated by user")
    except Exception as e:
        logger.error(f"Service crashed: {e}", exc_info=True)
        sys.exit(1)
