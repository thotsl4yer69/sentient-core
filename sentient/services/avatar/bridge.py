"""
Avatar Bridge Service - Sentient Core v2

Bridge between the cognitive system and avatar visualization.
Subscribes to emotional/conversational state changes via MQTT and publishes
avatar-specific commands (expression, phonemes, attention, idle animations).
Runs a WebSocket server so browser-based avatar UIs receive real-time updates.

Features:
- Emotion-driven color and expression changes (10 emotion states)
- Breathing/pulse idle animation
- Periodic blinking with randomized intervals
- Attention wandering when idle, snap-to-center on wake word
- WebSocket server forwarding MQTT state to browser clients
- Chat message relay from browser -> MQTT
- Smooth state transitions
"""

import asyncio
import json
import math
import random
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, Tuple, Set

try:
    import websockets
    import websockets.exceptions
except ImportError:
    websockets = None

from sentient.config import get_config
from sentient.common.service_base import SentientService
from sentient.common import mqtt_topics


# ---------------------------------------------------------------------------
# Timing constants
# ---------------------------------------------------------------------------

IDLE_ANIMATION_INTERVAL = 3.0       # seconds between idle ticks
BREATHING_CYCLE_DURATION = 4.0      # seconds for one full breath
BLINK_MIN_INTERVAL = 2.0            # minimum seconds between blinks
BLINK_MAX_INTERVAL = 6.0            # maximum seconds between blinks


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class EmotionState(str, Enum):
    """Supported emotion states for the avatar."""
    NEUTRAL = "neutral"
    HAPPY = "happy"
    AMUSED = "amused"
    CONCERNED = "concerned"
    FOCUSED = "focused"
    CURIOUS = "curious"
    PROTECTIVE = "protective"
    AFFECTIONATE = "affectionate"
    THOUGHTFUL = "thoughtful"
    ALERT = "alert"


@dataclass
class EmotionConfig:
    """Visual configuration for each emotion."""
    color: Tuple[int, int, int]       # RGB colour for avatar glow / ring
    expression: str                    # Expression identifier
    intensity_multiplier: float        # Animation intensity modifier


# Full emotion -> visual mapping
EMOTION_CONFIGS: Dict[EmotionState, EmotionConfig] = {
    EmotionState.NEUTRAL: EmotionConfig(
        color=(100, 150, 255),
        expression="neutral",
        intensity_multiplier=0.5,
    ),
    EmotionState.HAPPY: EmotionConfig(
        color=(255, 200, 100),
        expression="smile",
        intensity_multiplier=0.8,
    ),
    EmotionState.AMUSED: EmotionConfig(
        color=(255, 180, 120),
        expression="grin",
        intensity_multiplier=0.9,
    ),
    EmotionState.CONCERNED: EmotionConfig(
        color=(150, 100, 200),
        expression="worried",
        intensity_multiplier=0.6,
    ),
    EmotionState.FOCUSED: EmotionConfig(
        color=(100, 200, 255),
        expression="focused",
        intensity_multiplier=0.7,
    ),
    EmotionState.CURIOUS: EmotionConfig(
        color=(150, 255, 150),
        expression="interested",
        intensity_multiplier=0.75,
    ),
    EmotionState.PROTECTIVE: EmotionConfig(
        color=(255, 100, 100),
        expression="alert",
        intensity_multiplier=0.9,
    ),
    EmotionState.AFFECTIONATE: EmotionConfig(
        color=(255, 150, 200),
        expression="warm",
        intensity_multiplier=0.8,
    ),
    EmotionState.THOUGHTFUL: EmotionConfig(
        color=(120, 120, 200),
        expression="contemplative",
        intensity_multiplier=0.6,
    ),
    EmotionState.ALERT: EmotionConfig(
        color=(255, 220, 100),
        expression="attentive",
        intensity_multiplier=1.0,
    ),
}


@dataclass
class AttentionVector:
    """Gaze / attention direction."""
    x: float          # -1.0 (left) to 1.0 (right)
    y: float          # -1.0 (down) to 1.0 (up)
    focus: float      # 0.0 (unfocused) to 1.0 (focused)
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class AvatarState:
    """Complete avatar state snapshot."""
    emotion: EmotionState = EmotionState.NEUTRAL
    emotion_intensity: float = 0.5
    speaking: bool = False
    thinking: bool = False
    attention: Optional[AttentionVector] = None
    breathing_phase: float = 0.0
    last_blink: float = 0.0

    def __post_init__(self):
        if self.attention is None:
            self.attention = AttentionVector(x=0.0, y=0.0, focus=0.5)
        if self.last_blink == 0.0:
            self.last_blink = time.time()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class AvatarBridgeService(SentientService):
    """
    Production-ready bridge between the cognitive system and the avatar UI.

    Extends SentientService for MQTT lifecycle, config, and structured logging.
    """

    def __init__(self):
        super().__init__(name="avatar-bridge")

        # Avatar state
        self.state = AvatarState()

        # WebSocket bookkeeping
        self._ws_server: Optional[Any] = None
        self._ws_clients: Set = set()
        self._ws_server_task: Optional[asyncio.Task] = None

        # Background animation tasks
        self._idle_task: Optional[asyncio.Task] = None

        # Activity tracking
        self._last_activity = time.time()
        self._last_emotion_publish = 0.0
        self._last_attention_publish = 0.0

        # -----------------------------------------------------------------
        # MQTT handlers (registered via base-class decorator)
        # -----------------------------------------------------------------

        @self.on_mqtt(mqtt_topics.AVATAR_EXPRESSION)
        async def _handle_emotion(topic: str, payload: bytes):
            await self._handle_emotion_message(payload)

        @self.on_mqtt(mqtt_topics.CONVERSATION_STATE)
        async def _handle_conversation(topic: str, payload: bytes):
            await self._handle_conversation_message(payload)

        @self.on_mqtt(mqtt_topics.AVATAR_THINKING)
        async def _handle_thinking(topic: str, payload: bytes):
            await self._handle_thinking_message(payload)

        @self.on_mqtt(mqtt_topics.TTS_STATUS)
        async def _handle_tts(topic: str, payload: bytes):
            await self._handle_tts_message(payload)

        @self.on_mqtt(mqtt_topics.WAKE_WORD_DETECTED)
        async def _handle_wake(topic: str, payload: bytes):
            await self._handle_wake_detected()

        @self.on_mqtt(mqtt_topics.CHAT_OUTPUT)
        async def _handle_chat_output(topic: str, payload: bytes):
            await self._forward_to_websockets(topic, payload)

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    async def setup(self):
        """Start the WebSocket server and idle animation loop."""
        self.logger.info("Setting up avatar bridge...")

        # Start WebSocket server
        self._ws_server_task = asyncio.create_task(self._run_websocket_server())
        self._tasks.append(self._ws_server_task)

        # Start idle animation loop
        self._idle_task = asyncio.create_task(self._idle_animation_loop())
        self._tasks.append(self._idle_task)

        # Allow the WS server a moment to bind
        await asyncio.sleep(0.3)

        # Publish initial neutral state
        await self._publish_emotion_update()
        await self._publish_attention_update()

        self.logger.info("Avatar bridge setup complete")

    async def teardown(self):
        """Stop background tasks and close WebSocket connections."""
        self.logger.info("Tearing down avatar bridge...")

        # Cancel idle animation
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
            try:
                await self._idle_task
            except asyncio.CancelledError:
                pass

        # Cancel WebSocket server
        if self._ws_server_task and not self._ws_server_task.done():
            self._ws_server_task.cancel()
            try:
                await self._ws_server_task
            except asyncio.CancelledError:
                pass

        # Close all WebSocket client connections
        if self._ws_clients:
            self.logger.info(f"Closing {len(self._ws_clients)} WebSocket connections")
            await asyncio.gather(
                *[client.close() for client in self._ws_clients],
                return_exceptions=True,
            )
            self._ws_clients.clear()

        self.logger.info("Avatar bridge teardown complete")

    # -----------------------------------------------------------------------
    # Publishing helpers
    # -----------------------------------------------------------------------

    async def _publish_and_broadcast(self, topic: str, payload: Dict[str, Any]):
        """Publish to MQTT and forward to all WebSocket clients."""
        await self.mqtt_publish(topic, payload)
        self.logger.debug(f"Published to {topic}")

        await self._broadcast_to_websockets({
            "topic": topic,
            "payload": payload,
            "timestamp": datetime.now().isoformat(),
        })

    async def _publish_emotion_update(self):
        """Publish the current emotion state."""
        config = EMOTION_CONFIGS.get(
            self.state.emotion, EMOTION_CONFIGS[EmotionState.NEUTRAL]
        )
        payload = {
            "emotion": self.state.emotion.value,
            "expression": config.expression,
            "color": {
                "r": config.color[0],
                "g": config.color[1],
                "b": config.color[2],
            },
            "intensity": self.state.emotion_intensity,
            "timestamp": time.time(),
        }
        await self._publish_and_broadcast(mqtt_topics.AVATAR_EXPRESSION, payload)
        self._last_emotion_publish = time.time()

    async def _publish_attention_update(self):
        """Publish the current attention vector."""
        payload = {
            "x": self.state.attention.x,
            "y": self.state.attention.y,
            "focus": self.state.attention.focus,
            "timestamp": self.state.attention.timestamp,
        }
        await self._publish_and_broadcast(mqtt_topics.AVATAR_ATTENTION, payload)
        self._last_attention_publish = time.time()

    # -----------------------------------------------------------------------
    # State mutation
    # -----------------------------------------------------------------------

    async def update_emotion(self, emotion: EmotionState, intensity: float = 0.7):
        """Update the avatar emotion and publish."""
        self.state.emotion = emotion
        self.state.emotion_intensity = max(0.0, min(1.0, intensity))
        await self._publish_emotion_update()
        self.logger.info(
            f"Emotion updated: {emotion.value} "
            f"(intensity: {self.state.emotion_intensity:.2f})"
        )

    async def set_speaking(self, speaking: bool):
        """Update speaking state."""
        if self.state.speaking == speaking:
            return
        self.state.speaking = speaking
        self._last_activity = time.time()

        await self._publish_and_broadcast(mqtt_topics.AVATAR_SPEAKING, {
            "speaking": speaking,
            "timestamp": time.time(),
        })

        if speaking:
            self.state.attention.focus = 0.9
            self.state.attention.x = 0.0
            self.state.attention.y = 0.1

        self.logger.info(f"Speaking state: {speaking}")

    async def set_thinking(self, thinking: bool, topic: str = ""):
        """Update thinking state."""
        if self.state.thinking == thinking:
            return
        self.state.thinking = thinking
        self._last_activity = time.time()

        if thinking and self.state.emotion == EmotionState.NEUTRAL:
            await self.update_emotion(EmotionState.THOUGHTFUL, intensity=0.6)

        if thinking:
            self.state.attention.x = random.uniform(-0.3, 0.3)
            self.state.attention.y = random.uniform(-0.2, 0.2)
            self.state.attention.focus = 0.4

        self.logger.info(f"Thinking state: {thinking} (topic: {topic})")

    async def update_attention(
        self,
        x: Optional[float] = None,
        y: Optional[float] = None,
        focus: Optional[float] = None,
    ):
        """Update gaze / attention direction."""
        if x is not None:
            self.state.attention.x = max(-1.0, min(1.0, x))
        if y is not None:
            self.state.attention.y = max(-1.0, min(1.0, y))
        if focus is not None:
            self.state.attention.focus = max(0.0, min(1.0, focus))
        self.state.attention.timestamp = time.time()
        await self._publish_attention_update()

    # -----------------------------------------------------------------------
    # MQTT message handlers
    # -----------------------------------------------------------------------

    async def _handle_emotion_message(self, payload: bytes):
        """Handle emotion state update from cognitive system.

        NOTE: We only update internal state and broadcast to WS clients.
        We do NOT re-publish to MQTT to avoid a feedback loop (we subscribe
        to the same topic we would publish to).
        """
        try:
            data = json.loads(payload.decode())
            emotion_str = data.get("emotion", "neutral")
            intensity = float(data.get("intensity", 0.7))
            try:
                emotion = EmotionState(emotion_str)
            except ValueError:
                self.logger.warning(f"Unknown emotion '{emotion_str}', using NEUTRAL")
                emotion = EmotionState.NEUTRAL

            # Update state without re-publishing to MQTT (prevents feedback loop)
            self.state.emotion = emotion
            self.state.emotion_intensity = max(0.0, min(1.0, intensity))

            # Only broadcast to WebSocket clients
            config = EMOTION_CONFIGS.get(emotion, EMOTION_CONFIGS[EmotionState.NEUTRAL])
            ws_payload = {
                "emotion": emotion.value,
                "expression": config.expression,
                "color": {
                    "r": config.color[0],
                    "g": config.color[1],
                    "b": config.color[2],
                },
                "intensity": self.state.emotion_intensity,
                "timestamp": time.time(),
            }
            await self._broadcast_to_websockets({
                "topic": "sentient/avatar/expression",
                "payload": ws_payload,
                "timestamp": datetime.now().isoformat(),
            })
            self.logger.info(
                f"Emotion updated: {emotion.value} "
                f"(intensity: {self.state.emotion_intensity:.2f})"
            )
        except Exception as e:
            self.logger.error(f"Error handling emotion message: {e}")

    async def _handle_conversation_message(self, payload: bytes):
        """Handle conversation state change -- detect speaking."""
        try:
            data = json.loads(payload.decode())

            state_value = data.get("state", "")
            if state_value == "responding":
                await self.set_speaking(True)
                if self.state.emotion == EmotionState.NEUTRAL:
                    await self.update_emotion(EmotionState.HAPPY, intensity=0.6)
                asyncio.create_task(self._auto_stop_speaking(delay=3.0))
            elif state_value in ("idle", "listening"):
                await self.set_speaking(False)
        except Exception as e:
            self.logger.error(f"Error handling conversation message: {e}")

    async def _handle_thinking_message(self, payload: bytes):
        """Handle thinking / deliberation state."""
        try:
            data = json.loads(payload.decode())
            is_thinking = data.get("is_thinking", False)
            topic = data.get("topic", "")
            await self.set_thinking(is_thinking, topic)
        except Exception as e:
            self.logger.error(f"Error handling thinking message: {e}")

    async def _handle_tts_message(self, payload: bytes):
        """Handle TTS status events -- start/stop speaking, forward phonemes."""
        try:
            data = json.loads(payload.decode())

            status = data.get("status", "")
            phonemes = data.get("phonemes", [])
            text = data.get("text", "")
            emotion_raw = data.get("emotion", "neutral")
            audio = data.get("audio", {})

            # Normalise emotion
            if isinstance(emotion_raw, dict):
                emotion = emotion_raw.get("type", "neutral")
                emotion_intensity = emotion_raw.get("intensity", 0.5)
            else:
                emotion = str(emotion_raw)
                emotion_intensity = 0.5

            if status == "speaking" or phonemes:
                await self.set_speaking(True)

            # Update emotion if non-neutral
            if emotion and emotion != "neutral":
                try:
                    emotion_state = EmotionState(emotion.lower())
                    await self.update_emotion(emotion_state, intensity=emotion_intensity)
                except ValueError:
                    self.logger.debug(f"Unknown emotion in TTS: {emotion}")

            # Forward phonemes for lip sync
            if phonemes:
                self.logger.info(f"Forwarding {len(phonemes)} phonemes for lip sync")
                await self._publish_and_broadcast(mqtt_topics.AVATAR_PHONEMES, {
                    "phonemes": phonemes,
                    "text": text,
                    "emotion": emotion,
                    "timestamp": time.time(),
                })

            # Auto-stop speaking after estimated duration
            if isinstance(audio, dict):
                audio_duration = audio.get("duration", 3.0)
            else:
                if phonemes:
                    last = phonemes[-1]
                    audio_duration = last.get("time", 0) + last.get("duration", 0.1) + 0.5
                else:
                    audio_duration = 3.0

            if status == "done":
                await self.set_speaking(False)
            else:
                asyncio.create_task(self._auto_stop_speaking(delay=audio_duration))

        except Exception as e:
            self.logger.error(f"Error handling TTS message: {e}", exc_info=True)

    async def _handle_wake_detected(self):
        """Handle wake word -- snap to attention."""
        self.logger.info("Wake word detected - activating alert mode")
        await self.update_emotion(EmotionState.ALERT, intensity=1.0)
        await self.update_attention(x=0.0, y=0.0, focus=1.0)
        self._last_activity = time.time()

    async def _forward_to_websockets(self, topic: str, payload: bytes):
        """Forward an MQTT message directly to WebSocket clients."""
        try:
            data = json.loads(payload.decode())
            await self._broadcast_to_websockets({
                "topic": topic,
                "payload": data,
                "timestamp": datetime.now().isoformat(),
            })
        except Exception as e:
            self.logger.error(f"Error forwarding to websockets: {e}")

    async def _auto_stop_speaking(self, delay: float):
        """Fallback: stop speaking after *delay* seconds."""
        await asyncio.sleep(delay)
        if self.state.speaking:
            await self.set_speaking(False)

    # -----------------------------------------------------------------------
    # Idle animation loop
    # -----------------------------------------------------------------------

    async def _idle_animation_loop(self):
        """
        Continuous idle animation loop.

        Implements:
        - Breathing pulse animation (sine wave)
        - Periodic blinking with randomised intervals
        - Subtle attention wandering when idle
        - Return-to-centre drift
        """
        self.logger.info("Starting idle animation loop")

        try:
            while self._running:
                current_time = time.time()

                # Breathing phase (sine wave)
                breathing_t = (
                    (current_time % BREATHING_CYCLE_DURATION) / BREATHING_CYCLE_DURATION
                )
                self.state.breathing_phase = (
                    math.sin(breathing_t * 2 * math.pi) + 1
                ) / 2

                breathing_payload = {
                    "phase": self.state.breathing_phase,
                    "intensity": 0.1,
                    "timestamp": current_time,
                }
                if self._ws_clients:
                    await self._publish_and_broadcast(
                        mqtt_topics.AVATAR_IDLE, breathing_payload
                    )

                # Blinking
                time_since_blink = current_time - self.state.last_blink
                blink_interval = random.uniform(BLINK_MIN_INTERVAL, BLINK_MAX_INTERVAL)
                if time_since_blink > blink_interval:
                    self.state.last_blink = current_time
                    self.logger.debug("Blink triggered")

                # Attention wandering when idle
                time_since_activity = current_time - self._last_activity

                if (
                    time_since_activity > 5.0
                    and not self.state.speaking
                    and not self.state.thinking
                ):
                    if random.random() < 0.1:
                        drift_x = random.uniform(-0.4, 0.4)
                        drift_y = random.uniform(-0.3, 0.2)
                        if self._ws_clients:
                            await self.update_attention(x=drift_x, y=drift_y, focus=0.3)
                        self.logger.debug(
                            f"Attention drift: ({drift_x:.2f}, {drift_y:.2f})"
                        )

                # Return to centre occasionally
                if time_since_activity > 15.0 and random.random() < 0.05:
                    if self._ws_clients:
                        await self.update_attention(x=0.0, y=0.0, focus=0.5)
                    self.logger.debug("Attention returned to centre")

                await asyncio.sleep(IDLE_ANIMATION_INTERVAL)

        except asyncio.CancelledError:
            self.logger.info("Idle animation loop cancelled")
        except Exception as e:
            self.logger.error(f"Error in idle animation loop: {e}", exc_info=True)

    # -----------------------------------------------------------------------
    # WebSocket server
    # -----------------------------------------------------------------------

    async def _run_websocket_server(self):
        """Run the WebSocket server for browser-based avatar clients."""
        if websockets is None:
            self.logger.warning(
                "websockets package not installed -- WebSocket server disabled"
            )
            return

        ws_port = self.config.avatar.bridge_ws_port
        self.logger.info(f"Starting WebSocket server on 0.0.0.0:{ws_port}")

        try:
            async def handler(ws):
                await self._websocket_handler(ws)

            self._ws_server = await websockets.serve(handler, "0.0.0.0", ws_port)
            self.logger.info(f"WebSocket server listening on ws://0.0.0.0:{ws_port}")

            # Keep alive until cancelled
            await asyncio.Future()

        except asyncio.CancelledError:
            self.logger.info("WebSocket server cancelled")
        except Exception as e:
            self.logger.error(f"WebSocket server error: {e}", exc_info=True)
        finally:
            if self._ws_server:
                self._ws_server.close()
                await self._ws_server.wait_closed()
                self.logger.info("WebSocket server stopped")

    async def _websocket_handler(self, ws):
        """Handle a single WebSocket client connection."""
        client_id = f"{ws.remote_address[0]}:{ws.remote_address[1]}"
        self.logger.info(f"WebSocket client connected: {client_id}")
        self._ws_clients.add(ws)

        try:
            # Send current state snapshot to the new client
            current_state = {
                "topic": "sentient/avatar/state",
                "payload": {
                    "emotion": self.state.emotion.value,
                    "emotion_intensity": self.state.emotion_intensity,
                    "speaking": self.state.speaking,
                    "thinking": self.state.thinking,
                    "attention": {
                        "x": self.state.attention.x,
                        "y": self.state.attention.y,
                        "focus": self.state.attention.focus,
                    },
                },
                "timestamp": datetime.now().isoformat(),
            }
            await ws.send(json.dumps(current_state))

            # Listen for incoming messages (chat relay)
            async for message in ws:
                try:
                    data = json.loads(message)

                    if data.get("type") == "chat":
                        chat_text = data.get("text", "")
                        user_name = data.get("user", "User")

                        if chat_text:
                            self.logger.info(
                                f"Chat message from {user_name}: {chat_text}"
                            )
                            chat_payload = {
                                "text": chat_text,
                                "user": user_name,
                                "timestamp": time.time(),
                            }
                            await self.mqtt_publish(
                                mqtt_topics.CHAT_INPUT, chat_payload
                            )
                            self.logger.info(
                                f"Published chat to {mqtt_topics.CHAT_INPUT}"
                            )

                except json.JSONDecodeError:
                    self.logger.warning(
                        f"Invalid JSON from client {client_id}: {message}"
                    )
                except Exception as e:
                    self.logger.error(
                        f"Error processing message from {client_id}: {e}"
                    )

        except websockets.exceptions.ConnectionClosed:
            self.logger.debug(
                f"WebSocket client disconnected normally: {client_id}"
            )
        except Exception as e:
            self.logger.error(f"WebSocket handler error for {client_id}: {e}")
        finally:
            self._ws_clients.discard(ws)
            self.logger.info(
                f"WebSocket client removed: {client_id} "
                f"(total: {len(self._ws_clients)})"
            )

    async def _broadcast_to_websockets(self, message: Dict[str, Any]):
        """Send a JSON message to all connected WebSocket clients."""
        if not self._ws_clients:
            return
        try:
            message_json = json.dumps(message)
            await asyncio.gather(
                *[
                    self._send_to_client(client, message_json)
                    for client in self._ws_clients
                ],
                return_exceptions=True,
            )
        except Exception as e:
            self.logger.error(f"Error broadcasting to WebSocket clients: {e}")

    async def _send_to_client(self, client, message: str):
        """Send a message to a single WebSocket client with error handling."""
        try:
            await client.send(message)
        except websockets.exceptions.ConnectionClosed:
            self.logger.debug("Client connection closed during send")
        except Exception as e:
            self.logger.error(f"Error sending to WebSocket client: {e}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    service = AvatarBridgeService()
    try:
        asyncio.run(service.run())
    except KeyboardInterrupt:
        service.logger.info("Service terminated by user")
    except Exception as e:
        service.logger.error(f"Service crashed: {e}", exc_info=True)
        sys.exit(1)
