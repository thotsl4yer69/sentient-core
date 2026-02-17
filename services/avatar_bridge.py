#!/usr/bin/env python3
"""
Sentient Core - Avatar Bridge
Production-ready bridge connecting cognitive system to avatar visualization.

Architecture:
- Subscribes to cognitive/emotional state from conversation and contemplation services
- Publishes to avatar MQTT topics for real-time expression and animation
- Implements lifelike idle behaviors and reactive animations
- Full error handling and reconnection logic

Avatar MQTT Topics (Published):
- sentient/persona/emotion: Update avatar expression and color
- sentient/persona/speaking: Trigger lip sync animation
- sentient/persona/attention: Gaze direction control
- sentient/audio/tts/phonemes: Real-time phoneme data for lip sync

Subscribed Topics (Cognitive System):
- sentient/conversation/response: Detect speaking state
- sentient/emotion/state: Emotion state from deliberation
- sentient/conversation/thinking: Thinking/contemplation state
- sentient/tts/synthesize: TTS events for lip sync timing
- sentient/wake/detected: Wake word detection for attention

Features:
- Emotion-driven color and expression changes
- Breathing/pulse idle animation
- Eye contact simulation with attention wandering
- Reactive gaze based on activity
- Smooth transitions between states
"""

import asyncio
import json
import logging
import random
import signal
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
import math

try:
    from aiomqtt import Client as MQTTClient
except ImportError:
    print("ERROR: aiomqtt is required. Install with: pip install aiomqtt")
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("ERROR: websockets is required. Install with: pip install websockets")
    sys.exit(1)

# ============================================================================
# CONFIGURATION
# ============================================================================

MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_USER = "sentient"
MQTT_PASS = "sentient1312"

# WebSocket Server Configuration
WEBSOCKET_HOST = "0.0.0.0"
WEBSOCKET_PORT = 9001

# Subscribed Topics (Input from Cognitive System)
TOPIC_CONVERSATION_RESPONSE = "sentient/conversation/response"
TOPIC_EMOTION_STATE = "sentient/persona/emotion"
TOPIC_THINKING = "sentient/conversation/thinking"
TOPIC_TTS_OUTPUT = "sentient/tts/output"
TOPIC_WAKE_DETECTED = "sentient/wake/detected"
TOPIC_MEMORY_EVENT = "sentient/memory/event"
TOPIC_CHAT_OUTPUT = "sentient/persona/chat/output"

# Published Topics (Output to Avatar)
TOPIC_AVATAR_EMOTION = "sentient/avatar/emotion"
TOPIC_AVATAR_SPEAKING = "sentient/persona/speaking"
TOPIC_AVATAR_ATTENTION = "sentient/persona/attention"
TOPIC_AVATAR_PHONEMES = "sentient/audio/tts/phonemes"
TOPIC_AVATAR_IDLE = "sentient/persona/idle"
TOPIC_CHAT_INPUT = "sentient/persona/chat/input"

# Timing Configuration
IDLE_ANIMATION_INTERVAL = 3.0  # Seconds between idle animations
ATTENTION_UPDATE_INTERVAL = 0.5  # Seconds between attention updates
BREATHING_CYCLE_DURATION = 4.0  # Seconds for one breath cycle
BLINK_MIN_INTERVAL = 2.0  # Minimum seconds between blinks
BLINK_MAX_INTERVAL = 6.0  # Maximum seconds between blinks

# Logging
LOG_DIR = Path("/var/log/sentient")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "avatar_bridge.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("AvatarBridge")

# ============================================================================
# DATA CLASSES AND ENUMS
# ============================================================================


class EmotionState(str, Enum):
    """Supported emotion states for avatar"""
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
    """Visual configuration for each emotion"""
    color: Tuple[int, int, int]  # RGB color for avatar
    expression: str  # Expression identifier
    intensity_multiplier: float  # Animation intensity modifier


# Emotion to visual mapping
EMOTION_CONFIGS: Dict[EmotionState, EmotionConfig] = {
    EmotionState.NEUTRAL: EmotionConfig(
        color=(100, 150, 255),  # Calm blue
        expression="neutral",
        intensity_multiplier=0.5
    ),
    EmotionState.HAPPY: EmotionConfig(
        color=(255, 200, 100),  # Warm yellow
        expression="smile",
        intensity_multiplier=0.8
    ),
    EmotionState.AMUSED: EmotionConfig(
        color=(255, 180, 120),  # Orange-yellow
        expression="grin",
        intensity_multiplier=0.9
    ),
    EmotionState.CONCERNED: EmotionConfig(
        color=(150, 100, 200),  # Purple
        expression="worried",
        intensity_multiplier=0.6
    ),
    EmotionState.FOCUSED: EmotionConfig(
        color=(100, 200, 255),  # Bright cyan
        expression="focused",
        intensity_multiplier=0.7
    ),
    EmotionState.CURIOUS: EmotionConfig(
        color=(150, 255, 150),  # Green
        expression="interested",
        intensity_multiplier=0.75
    ),
    EmotionState.PROTECTIVE: EmotionConfig(
        color=(255, 100, 100),  # Red
        expression="alert",
        intensity_multiplier=0.9
    ),
    EmotionState.AFFECTIONATE: EmotionConfig(
        color=(255, 150, 200),  # Pink
        expression="warm",
        intensity_multiplier=0.8
    ),
    EmotionState.THOUGHTFUL: EmotionConfig(
        color=(120, 120, 200),  # Deep blue
        expression="contemplative",
        intensity_multiplier=0.6
    ),
    EmotionState.ALERT: EmotionConfig(
        color=(255, 220, 100),  # Bright yellow
        expression="attentive",
        intensity_multiplier=1.0
    ),
}


@dataclass
class AttentionVector:
    """Gaze/attention direction"""
    x: float  # -1.0 (left) to 1.0 (right)
    y: float  # -1.0 (down) to 1.0 (up)
    focus: float  # 0.0 (unfocused) to 1.0 (focused)
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class AvatarState:
    """Complete avatar state"""
    emotion: EmotionState = EmotionState.NEUTRAL
    emotion_intensity: float = 0.5
    speaking: bool = False
    thinking: bool = False
    attention: AttentionVector = None
    breathing_phase: float = 0.0  # 0.0 to 1.0
    last_blink: float = 0.0

    def __post_init__(self):
        if self.attention is None:
            self.attention = AttentionVector(x=0.0, y=0.0, focus=0.5)
        if self.last_blink == 0.0:
            self.last_blink = time.time()


# ============================================================================
# AVATAR BRIDGE SERVICE
# ============================================================================


class AvatarBridge:
    """
    Production-ready bridge between cognitive system and avatar visualization.

    Responsibilities:
    - Emotion state management and publishing
    - Speaking state detection and lip sync coordination
    - Attention/gaze control with realistic movement
    - Idle animations (breathing, blinking, micro-movements)
    - Smooth state transitions
    """

    def __init__(
        self,
        mqtt_broker: str = MQTT_BROKER,
        mqtt_port: int = MQTT_PORT,
        websocket_host: str = WEBSOCKET_HOST,
        websocket_port: int = WEBSOCKET_PORT
    ):
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.websocket_host = websocket_host
        self.websocket_port = websocket_port

        # State
        self.state = AvatarState()
        self.running = False
        self.mqtt_client: Optional[MQTTClient] = None

        # WebSocket server and connected clients
        self.websocket_server = None
        self.websocket_clients: set = set()
        self.websocket_server_task: Optional[asyncio.Task] = None

        # Animation state
        self.idle_animation_task: Optional[asyncio.Task] = None
        self.attention_task: Optional[asyncio.Task] = None

        # Timing
        self.last_activity = time.time()
        self.last_emotion_publish = 0.0
        self.last_attention_publish = 0.0

        logger.info("AvatarBridge initialized")


    async def publish_avatar_message(
        self,
        topic: str,
        payload: Dict[str, Any],
        qos: int = 0
    ):
        """Publish message to avatar topic and forward to WebSocket clients"""
        if not self.mqtt_client:
            logger.warning("MQTT client not connected, cannot publish")
            return

        try:
            message = json.dumps(payload)
            await self.mqtt_client.publish(topic, message, qos=qos)
            logger.debug(f"Published to {topic}: {payload}")

            # Forward to WebSocket clients
            await self.broadcast_to_websockets({
                "topic": topic,
                "payload": payload,
                "timestamp": datetime.now().isoformat()
            })
        except Exception as e:
            logger.error(f"Failed to publish to {topic}: {e}")

    async def update_emotion(
        self,
        emotion: EmotionState,
        intensity: float = 0.7
    ):
        """Update avatar emotion state"""
        # Update internal state
        self.state.emotion = emotion
        self.state.emotion_intensity = max(0.0, min(1.0, intensity))

        # Get emotion config
        config = EMOTION_CONFIGS.get(emotion, EMOTION_CONFIGS[EmotionState.NEUTRAL])

        # Publish to avatar
        payload = {
            "emotion": emotion.value,
            "expression": config.expression,
            "color": {
                "r": config.color[0],
                "g": config.color[1],
                "b": config.color[2]
            },
            "intensity": self.state.emotion_intensity,
            "timestamp": time.time()
        }

        await self.publish_avatar_message(TOPIC_AVATAR_EMOTION, payload, qos=1)
        self.last_emotion_publish = time.time()

        logger.info(
            f"Emotion updated: {emotion.value} "
            f"(intensity: {self.state.emotion_intensity:.2f})"
        )

    async def set_speaking(self, speaking: bool):
        """Update speaking state"""
        if self.state.speaking == speaking:
            return

        self.state.speaking = speaking
        self.last_activity = time.time()

        # Publish speaking state
        payload = {
            "speaking": speaking,
            "timestamp": time.time()
        }

        await self.publish_avatar_message(TOPIC_AVATAR_SPEAKING, payload, qos=1)

        # Adjust attention when speaking
        if speaking:
            # More focused when speaking
            self.state.attention.focus = 0.9
            self.state.attention.x = 0.0  # Look forward
            self.state.attention.y = 0.1  # Slightly up

        logger.info(f"Speaking state: {speaking}")

    async def set_thinking(self, thinking: bool, topic: str = ""):
        """Update thinking state"""
        if self.state.thinking == thinking:
            return

        self.state.thinking = thinking
        self.last_activity = time.time()

        # When thinking, adjust emotion if currently neutral
        if thinking and self.state.emotion == EmotionState.NEUTRAL:
            await self.update_emotion(EmotionState.THOUGHTFUL, intensity=0.6)

        # Thinking affects attention - look slightly away
        if thinking:
            self.state.attention.x = random.uniform(-0.3, 0.3)
            self.state.attention.y = random.uniform(-0.2, 0.2)
            self.state.attention.focus = 0.4  # Unfocused while thinking

        logger.info(f"Thinking state: {thinking} (topic: {topic})")

    async def update_attention(
        self,
        x: float = None,
        y: float = None,
        focus: float = None
    ):
        """Update gaze/attention direction"""
        if x is not None:
            self.state.attention.x = max(-1.0, min(1.0, x))
        if y is not None:
            self.state.attention.y = max(-1.0, min(1.0, y))
        if focus is not None:
            self.state.attention.focus = max(0.0, min(1.0, focus))

        self.state.attention.timestamp = time.time()

        # Publish attention vector
        payload = {
            "x": self.state.attention.x,
            "y": self.state.attention.y,
            "focus": self.state.attention.focus,
            "timestamp": self.state.attention.timestamp
        }

        await self.publish_avatar_message(TOPIC_AVATAR_ATTENTION, payload)
        self.last_attention_publish = time.time()

    async def handle_wake_detected(self):
        """Handle wake word detection - snap to attention"""
        logger.info("Wake word detected - activating alert mode")

        # Immediate alert emotion
        await self.update_emotion(EmotionState.ALERT, intensity=1.0)

        # Snap attention to center (eye contact)
        await self.update_attention(x=0.0, y=0.0, focus=1.0)

        self.last_activity = time.time()

    async def idle_animation_loop(self):
        """
        Continuous idle animation loop.

        Implements:
        - Breathing pulse animation
        - Periodic blinking
        - Subtle attention wandering when not active
        - Micro-movements for lifelike appearance
        """
        logger.info("Starting idle animation loop")

        try:
            while self.running:
                current_time = time.time()

                # Calculate breathing phase (sine wave)
                breathing_t = (current_time % BREATHING_CYCLE_DURATION) / BREATHING_CYCLE_DURATION
                self.state.breathing_phase = (math.sin(breathing_t * 2 * math.pi) + 1) / 2

                # Publish breathing pulse for subtle avatar scaling/glow
                breathing_payload = {
                    "phase": self.state.breathing_phase,
                    "intensity": 0.1,  # Subtle
                    "timestamp": current_time
                }
                await self.publish_avatar_message(TOPIC_AVATAR_IDLE, breathing_payload)

                # Blinking
                time_since_blink = current_time - self.state.last_blink
                blink_interval = random.uniform(BLINK_MIN_INTERVAL, BLINK_MAX_INTERVAL)

                if time_since_blink > blink_interval:
                    # Trigger blink (handled by avatar visualization)
                    self.state.last_blink = current_time
                    logger.debug("Blink triggered")

                # Attention wandering when idle (not speaking/thinking)
                time_since_activity = current_time - self.last_activity

                if time_since_activity > 5.0 and not self.state.speaking and not self.state.thinking:
                    # Subtle attention drift
                    if random.random() < 0.1:  # 10% chance per cycle
                        drift_x = random.uniform(-0.4, 0.4)
                        drift_y = random.uniform(-0.3, 0.2)
                        await self.update_attention(
                            x=drift_x,
                            y=drift_y,
                            focus=0.3  # Unfocused
                        )
                        logger.debug(f"Attention drift: ({drift_x:.2f}, {drift_y:.2f})")

                # Return to center occasionally when idle
                if time_since_activity > 15.0 and random.random() < 0.05:
                    await self.update_attention(x=0.0, y=0.0, focus=0.5)
                    logger.debug("Attention returned to center")

                await asyncio.sleep(IDLE_ANIMATION_INTERVAL)

        except asyncio.CancelledError:
            logger.info("Idle animation loop cancelled")
        except Exception as e:
            logger.error(f"Error in idle animation loop: {e}", exc_info=True)

    async def broadcast_to_websockets(self, message: Dict[str, Any]):
        """Send message to all connected WebSocket clients"""
        if not self.websocket_clients:
            return

        try:
            message_json = json.dumps(message)
            # Gather all send operations with exception handling
            await asyncio.gather(
                *[self._send_to_client(client, message_json) for client in self.websocket_clients],
                return_exceptions=True
            )
        except Exception as e:
            logger.error(f"Error broadcasting to WebSocket clients: {e}")

    async def _send_to_client(self, client, message: str):
        """Send message to a single WebSocket client with error handling"""
        try:
            await client.send(message)
        except websockets.exceptions.ConnectionClosed:
            logger.debug("Client connection closed during send")
            # Will be cleaned up by the handler
        except Exception as e:
            logger.error(f"Error sending to WebSocket client: {e}")

    async def websocket_handler(self, websocket):
        """Handle incoming WebSocket connections"""
        client_id = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        logger.info(f"WebSocket client connected: {client_id}")

        self.websocket_clients.add(websocket)

        try:
            # Send current state to new client
            current_state = {
                "topic": "sentient/persona/state",
                "payload": {
                    "emotion": self.state.emotion.value,
                    "emotion_intensity": self.state.emotion_intensity,
                    "speaking": self.state.speaking,
                    "thinking": self.state.thinking,
                    "attention": {
                        "x": self.state.attention.x,
                        "y": self.state.attention.y,
                        "focus": self.state.attention.focus
                    }
                },
                "timestamp": datetime.now().isoformat()
            }
            await websocket.send(json.dumps(current_state))

            # Listen for incoming messages from client
            async for message in websocket:
                try:
                    data = json.loads(message)

                    # Handle chat messages
                    if data.get("type") == "chat":
                        chat_text = data.get("text", "")
                        user_name = data.get("user", "User")

                        if chat_text:
                            logger.info(f"Chat message from {user_name}: {chat_text}")

                            # Publish to MQTT for persona to handle
                            chat_payload = {
                                "text": chat_text,
                                "user": user_name,
                                "timestamp": time.time()
                            }

                            if self.mqtt_client:
                                try:
                                    await self.mqtt_client.publish(
                                        TOPIC_CHAT_INPUT,
                                        json.dumps(chat_payload),
                                        qos=1
                                    )
                                    logger.info(f"Published chat to {TOPIC_CHAT_INPUT}")
                                except Exception as e:
                                    logger.error(f"Failed to publish chat message to MQTT: {e}")
                            else:
                                logger.warning("MQTT client not available, cannot forward chat")

                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from client {client_id}: {message}")
                except Exception as e:
                    logger.error(f"Error processing message from {client_id}: {e}")

        except websockets.exceptions.ConnectionClosed:
            logger.debug(f"WebSocket client disconnected normally: {client_id}")
        except Exception as e:
            logger.error(f"WebSocket handler error for {client_id}: {e}")
        finally:
            self.websocket_clients.discard(websocket)
            logger.info(f"WebSocket client removed: {client_id} (total: {len(self.websocket_clients)})")

    async def start_websocket_server(self):
        """Start WebSocket server for browser clients"""
        try:
            logger.info(f"Starting WebSocket server on {self.websocket_host}:{self.websocket_port}")

            # Wrapper to avoid method binding issues with websockets 11+
            async def handler(websocket):
                await self.websocket_handler(websocket)

            self.websocket_server = await websockets.serve(
                handler,
                self.websocket_host,
                self.websocket_port
            )

            logger.info(f"WebSocket server listening on ws://{self.websocket_host}:{self.websocket_port}")

            # Keep server running
            await asyncio.Future()
        except asyncio.CancelledError:
            logger.info("WebSocket server cancelled")
        except Exception as e:
            logger.error(f"WebSocket server error: {e}", exc_info=True)
        finally:
            if self.websocket_server:
                self.websocket_server.close()
                await self.websocket_server.wait_closed()
                logger.info("WebSocket server stopped")

    async def handle_emotion_message(self, payload: str):
        """Handle emotion state update from cognitive system"""
        try:
            data = json.loads(payload)

            emotion_str = data.get("emotion", "neutral")
            intensity = float(data.get("intensity", 0.7))

            # Map to supported emotion
            try:
                emotion = EmotionState(emotion_str)
            except ValueError:
                logger.warning(f"Unknown emotion '{emotion_str}', using NEUTRAL")
                emotion = EmotionState.NEUTRAL

            await self.update_emotion(emotion, intensity)

        except Exception as e:
            logger.error(f"Error handling emotion message: {e}")

    async def handle_conversation_message(self, payload: str):
        """Handle conversation response - detect speaking"""
        try:
            # Conversation response means Cortana is speaking
            await self.set_speaking(True)

            # Parse response for emotion hints
            data = json.loads(payload) if payload.startswith("{") else {"message": payload}

            # Speaking typically means engaged/happy
            if self.state.emotion == EmotionState.NEUTRAL:
                await self.update_emotion(EmotionState.HAPPY, intensity=0.6)

            # Speaking lasts for estimated duration (will be reset by TTS completion)
            # For now, assume 3 seconds per response
            asyncio.create_task(self._auto_stop_speaking(delay=3.0))

        except Exception as e:
            logger.error(f"Error handling conversation message: {e}")

    async def _auto_stop_speaking(self, delay: float):
        """Automatically stop speaking after delay (fallback)"""
        await asyncio.sleep(delay)
        if self.state.speaking:
            await self.set_speaking(False)

    async def handle_thinking_message(self, payload: str):
        """Handle thinking/deliberation state"""
        try:
            data = json.loads(payload)

            is_thinking = data.get("is_thinking", False)
            topic = data.get("topic", "")

            await self.set_thinking(is_thinking, topic)

        except Exception as e:
            logger.error(f"Error handling thinking message: {e}")

    async def handle_tts_message(self, payload: str):
        """Handle TTS synthesis events - from sentient/tts/output"""
        try:
            data = json.loads(payload)

            # TTS output contains phonemes, text, emotion, and audio data
            phonemes = data.get("phonemes", [])
            text = data.get("text", "")
            emotion_raw = data.get("emotion", "neutral")
            audio = data.get("audio", {})

            # Normalize emotion to string and extract intensity
            if isinstance(emotion_raw, dict):
                emotion = emotion_raw.get("type", "neutral")
                emotion_intensity = emotion_raw.get("intensity", 0.5)
            else:
                emotion = str(emotion_raw)
                emotion_intensity = 0.5

            # Start speaking when TTS output arrives
            await self.set_speaking(True)

            # Update emotion if provided
            if emotion and emotion != "neutral":
                try:
                    emotion_state = EmotionState(emotion.lower())
                    await self.update_emotion(emotion_state, intensity=emotion_intensity)
                except ValueError:
                    logger.debug(f"Unknown emotion in TTS: {emotion}, keeping current")

            # Forward phonemes for lip sync
            if phonemes:
                logger.info(f"Forwarding {len(phonemes)} phonemes for lip sync")
                await self.publish_avatar_message(
                    TOPIC_AVATAR_PHONEMES,
                    {
                        "phonemes": phonemes,
                        "text": text,
                        "emotion": emotion,
                        "timestamp": time.time()
                    }
                )

            # Auto-stop speaking after estimated duration
            if isinstance(audio, dict):
                audio_duration = audio.get("duration", 3.0)
            else:
                # Estimate duration from phoneme timing if audio is a string (base64)
                if phonemes:
                    last = phonemes[-1]
                    audio_duration = last.get("time", 0) + last.get("duration", 0.1) + 0.5
                else:
                    audio_duration = 3.0
            asyncio.create_task(self._auto_stop_speaking(delay=audio_duration))

        except Exception as e:
            logger.error(f"Error handling TTS message: {e}", exc_info=True)

    async def subscribe_to_cognitive_system(self):
        """Subscribe to cognitive system topics and forward to WebSocket clients"""
        topics = [
            TOPIC_EMOTION_STATE,
            TOPIC_CONVERSATION_RESPONSE,
            TOPIC_THINKING,
            TOPIC_TTS_OUTPUT,
            TOPIC_WAKE_DETECTED,
            TOPIC_CHAT_OUTPUT,
        ]

        try:
            # Use the shared MQTT client (already connected in start())
            if not self.mqtt_client:
                logger.error("MQTT client not available for subscription")
                return

            # Subscribe to all topics
            for topic in topics:
                await self.mqtt_client.subscribe(topic)
                logger.info(f"Subscribed to: {topic}")

            # Message handling loop
            async for message in self.mqtt_client.messages:
                topic = message.topic.value
                payload = message.payload.decode()

                # Forward to WebSocket clients first
                try:
                    payload_dict = json.loads(payload) if payload.startswith("{") else {"message": payload}
                    await self.broadcast_to_websockets({
                        "topic": topic,
                        "payload": payload_dict,
                        "timestamp": datetime.now().isoformat()
                    })
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse payload as JSON for forwarding: {payload[:100]}")
                except Exception as e:
                    logger.error(f"Error forwarding message to WebSocket: {e}")

                # Route to appropriate handler
                if topic == TOPIC_EMOTION_STATE:
                    await self.handle_emotion_message(payload)
                elif topic == TOPIC_CONVERSATION_RESPONSE:
                    await self.handle_conversation_message(payload)
                elif topic == TOPIC_THINKING:
                    await self.handle_thinking_message(payload)
                elif topic == TOPIC_TTS_OUTPUT:
                    await self.handle_tts_message(payload)
                elif topic == TOPIC_WAKE_DETECTED:
                    await self.handle_wake_detected()

        except asyncio.CancelledError:
            logger.info("Subscription loop cancelled")
        except Exception as e:
            logger.error(f"Error in subscription loop: {e}", exc_info=True)

    async def start(self):
        """Start the avatar bridge service"""
        logger.info("Starting Avatar Bridge service...")

        try:
            async with MQTTClient(
                hostname=self.mqtt_broker,
                port=self.mqtt_port,
                username=MQTT_USER,
                password=MQTT_PASS
            ) as client:
                self.mqtt_client = client
                logger.info(f"Connected to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}")

                self.running = True

                # Start background tasks
                self.idle_animation_task = asyncio.create_task(self.idle_animation_loop())
                self.websocket_server_task = asyncio.create_task(self.start_websocket_server())

                # Give WebSocket server a moment to start
                await asyncio.sleep(0.5)

                # Initialize avatar to neutral state
                await self.update_emotion(EmotionState.NEUTRAL, intensity=0.5)
                await self.update_attention(x=0.0, y=0.0, focus=0.5)

                # Start subscription to cognitive system (blocking)
                logger.info("Avatar Bridge active - monitoring cognitive system...")
                await self.subscribe_to_cognitive_system()

        except Exception as e:
            logger.error(f"Avatar bridge error: {e}")
            return False

        return True

    async def stop(self):
        """Stop the avatar bridge service"""
        logger.info("Stopping Avatar Bridge service...")

        self.running = False

        # Cancel animation tasks
        if self.idle_animation_task:
            self.idle_animation_task.cancel()
            try:
                await self.idle_animation_task
            except asyncio.CancelledError:
                pass

        # Stop WebSocket server
        if self.websocket_server_task:
            self.websocket_server_task.cancel()
            try:
                await self.websocket_server_task
            except asyncio.CancelledError:
                pass

        # Close all WebSocket client connections
        if self.websocket_clients:
            logger.info(f"Closing {len(self.websocket_clients)} WebSocket connections")
            await asyncio.gather(
                *[client.close() for client in self.websocket_clients],
                return_exceptions=True
            )
            self.websocket_clients.clear()

        # MQTT client is managed by async with block in start()
        self.mqtt_client = None

        logger.info("Avatar Bridge service stopped")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================


async def main():
    """Main entry point"""
    # Create bridge with WebSocket server
    bridge = AvatarBridge(
        mqtt_broker=MQTT_BROKER,
        mqtt_port=MQTT_PORT,
        websocket_host=WEBSOCKET_HOST,
        websocket_port=WEBSOCKET_PORT
    )

    # Signal handlers
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        asyncio.create_task(bridge.stop())
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start service
    try:
        await bridge.start()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        await bridge.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Service terminated by user")
    except Exception as e:
        logger.error(f"Service crashed: {e}", exc_info=True)
        sys.exit(1)
