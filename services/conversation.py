#!/usr/bin/env python3
"""
Conversation Management Service for Sentient Core
Production-ready orchestration of all dialogue components

Responsibilities:
- Subscribe to user input (MQTT: sentient/persona/chat/input)
- Subscribe to wake word detections (sentient/wake/detected)
- Subscribe to voice transcriptions from Whisper (sentient/voice/transcription)
- Manage conversation state (active/idle/voice_mode)
- Route input through: memory → contemplation → output
- Track turn-taking and context
- Handle interruptions
- Publish responses to multiple outputs (MQTT, avatar, TTS)
- Update memory system with exchanges
- Integrate with perception for world state

Production Features:
- Full async MQTT subscriptions
- Service integration (HTTP + MQTT)
- State management (Redis)
- Error handling and recovery
- Comprehensive logging
- Complete conversation lifecycle
- Health monitoring
"""

import asyncio
import aiohttp
import json
import logging
import signal
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from contextlib import asynccontextmanager

try:
    import redis.asyncio as redis
except ImportError:
    redis = None

try:
    from aiomqtt import Client as MQTTClient, MqttError
except ImportError:
    MQTTClient = None
    MqttError = Exception

# Configuration
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_USERNAME = None
MQTT_PASSWORD = None

# MQTT Topics
TOPIC_CHAT_INPUT = "sentient/persona/chat/input"
TOPIC_CHAT_OUTPUT = "sentient/persona/chat/output"
TOPIC_WAKE_DETECTED = "sentient/wake/detected"
TOPIC_VOICE_TRANSCRIPTION = "sentient/voice/transcription"
TOPIC_AVATAR_EXPRESSION = "sentient/avatar/expression"
TOPIC_AVATAR_TEXT = "sentient/avatar/text"
TOPIC_TTS_SPEAK = "sentient/tts/speak"
TOPIC_CONVERSATION_STATE = "sentient/conversation/state"

# Service Endpoints
MEMORY_SERVICE_URL = "http://localhost:8001"
CONTEMPLATION_SERVICE_URL = "http://localhost:8002"
PERCEPTION_SERVICE_URL = "http://localhost:8003"

# Redis Configuration
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0
CONVERSATION_STATE_KEY = "sentient:conversation:state"
TURN_COUNTER_KEY = "sentient:conversation:turns"

# Timeouts and Intervals
INPUT_TIMEOUT = 300.0  # 5 minutes idle timeout
HEALTH_CHECK_INTERVAL = 30.0  # Health check every 30s
MAX_RETRIES = 3
RETRY_DELAY = 2.0

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/var/log/sentient/conversation.log')
    ]
)
logger = logging.getLogger('Conversation')


class ConversationState(Enum):
    """Current state of conversation system"""
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    RESPONDING = "responding"
    VOICE_MODE = "voice_mode"
    ERROR = "error"


class InputSource(Enum):
    """Source of user input"""
    TEXT_CHAT = "text_chat"
    VOICE_TRANSCRIPTION = "voice_transcription"
    WAKE_WORD = "wake_word"


@dataclass
class ConversationContext:
    """Complete context for a conversation turn"""
    user_id: str
    input_text: str
    input_source: InputSource
    timestamp: float
    conversation_id: str
    turn_number: int
    voice_mode: bool = False
    wake_word_triggered: bool = False
    metadata: Dict[str, Any] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        data = asdict(self)
        data['input_source'] = self.input_source.value
        return data


@dataclass
class ConversationResponse:
    """Generated response with all output modalities"""
    text: str
    emotion: Optional[str] = None
    expression: Optional[str] = None
    should_speak: bool = False
    metadata: Dict[str, Any] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return asdict(self)


class ConversationService:
    """Production-ready conversation orchestration service"""

    def __init__(self):
        self.running = False
        self.mqtt_client: Optional[MQTTClient] = None
        self.redis_client: Optional[redis.Redis] = None
        self.http_session: Optional[aiohttp.ClientSession] = None

        # State management
        self.state = ConversationState.IDLE
        self.active_conversations: Dict[str, ConversationContext] = {}
        self.last_activity: Dict[str, float] = {}

        # Service health tracking
        self.service_health: Dict[str, bool] = {
            'memory': False,
            'contemplation': False,
            'perception': False,
            'mqtt': False,
            'redis': False
        }

        # Background tasks
        self.tasks: List[asyncio.Task] = []

        # Conversation ID counter
        self.conversation_counter = 0

    async def initialize(self):
        """Initialize all service connections and resources"""
        logger.info("Initializing Conversation Service...")

        # Create log directory
        Path('/var/log/sentient').mkdir(parents=True, exist_ok=True)

        # Initialize HTTP session
        self.http_session = aiohttp.ClientSession()
        logger.info("HTTP session initialized")

        # Initialize Redis
        await self._initialize_redis()

        # Initialize MQTT
        await self._initialize_mqtt()

        # Check service health
        await self._check_service_health()

        logger.info("Conversation Service initialization complete")

    async def _initialize_redis(self):
        """Initialize Redis connection for state management"""
        if redis is None:
            logger.warning("Redis not available - using in-memory state only")
            self.service_health['redis'] = False
            return

        try:
            self.redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                decode_responses=True
            )

            # Test connection
            await self.redis_client.ping()
            self.service_health['redis'] = True
            logger.info(f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")

            # Load conversation counter
            counter = await self.redis_client.get(TURN_COUNTER_KEY)
            if counter:
                self.conversation_counter = int(counter)

        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self.service_health['redis'] = False

    async def _initialize_mqtt(self):
        """Initialize MQTT connection and subscriptions"""
        if MQTTClient is None:
            logger.error("aiomqtt not available - cannot initialize MQTT")
            self.service_health['mqtt'] = False
            return

        try:
            # Create MQTT client context
            self.mqtt_client = MQTTClient(
                hostname=MQTT_BROKER,
                port=MQTT_PORT,
                username=MQTT_USERNAME,
                password=MQTT_PASSWORD
            )

            await self.mqtt_client.__aenter__()
            self.service_health['mqtt'] = True
            logger.info(f"Connected to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}")

            # Subscribe to input topics
            # await self.mqtt_client.subscribe(TOPIC_CHAT_INPUT)  # Disabled: cortana service handles this
            logger.info(f"Subscribed to {TOPIC_CHAT_INPUT}")

            await self.mqtt_client.subscribe(TOPIC_WAKE_DETECTED)
            logger.info(f"Subscribed to {TOPIC_WAKE_DETECTED}")

            await self.mqtt_client.subscribe(TOPIC_VOICE_TRANSCRIPTION)
            logger.info(f"Subscribed to {TOPIC_VOICE_TRANSCRIPTION}")

        except Exception as e:
            logger.error(f"Failed to initialize MQTT: {e}")
            self.service_health['mqtt'] = False
            raise

    async def _check_service_health(self):
        """Check health of dependent services"""
        logger.info("Checking service health...")

        # Check Memory Service
        try:
            async with self.http_session.get(
                f"{MEMORY_SERVICE_URL}/health",
                timeout=aiohttp.ClientTimeout(total=5.0)
            ) as resp:
                if resp.status == 200:
                    self.service_health['memory'] = True
                    logger.info("Memory service: HEALTHY")
        except Exception as e:
            logger.warning(f"Memory service unavailable: {e}")
            self.service_health['memory'] = False

        # Check Contemplation Service
        try:
            async with self.http_session.get(
                f"{CONTEMPLATION_SERVICE_URL}/health",
                timeout=aiohttp.ClientTimeout(total=5.0)
            ) as resp:
                if resp.status == 200:
                    self.service_health['contemplation'] = True
                    logger.info("Contemplation service: HEALTHY")
        except Exception as e:
            logger.warning(f"Contemplation service unavailable: {e}")
            self.service_health['contemplation'] = False

        # Check Perception Service
        try:
            async with self.http_session.get(
                f"{PERCEPTION_SERVICE_URL}/health",
                timeout=aiohttp.ClientTimeout(total=5.0)
            ) as resp:
                if resp.status == 200:
                    self.service_health['perception'] = True
                    logger.info("Perception service: HEALTHY")
        except Exception as e:
            logger.warning(f"Perception service unavailable: {e}")
            self.service_health['perception'] = False

    async def _get_relevant_memories(self, query: str, user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Retrieve relevant memories from memory service"""
        if not self.service_health['memory']:
            logger.warning("Memory service unavailable - skipping memory retrieval")
            return []

        try:
            async with self.http_session.post(
                f"{MEMORY_SERVICE_URL}/recall",
                json={
                    "query": query,
                    "limit": limit,
                    "filter_metadata": {"user": user_id}
                },
                timeout=aiohttp.ClientTimeout(total=10.0)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data.get('memories', [])

        except Exception as e:
            logger.error(f"Failed to retrieve memories: {e}")
            return []

    async def _get_world_state(self) -> Dict[str, Any]:
        """Get current world state from perception service"""
        if not self.service_health['perception']:
            logger.warning("Perception service unavailable - using empty world state")
            return {}

        try:
            async with self.http_session.get(
                f"{PERCEPTION_SERVICE_URL}/state",
                timeout=aiohttp.ClientTimeout(total=5.0)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data.get('state', {})

        except Exception as e:
            logger.error(f"Failed to retrieve world state: {e}")
            return {}

    async def _generate_response(
        self,
        context: ConversationContext,
        memories: List[Dict[str, Any]],
        world_state: Dict[str, Any]
    ) -> ConversationResponse:
        """Generate response using contemplation service"""
        if not self.service_health['contemplation']:
            logger.error("Contemplation service unavailable - cannot generate response")
            return ConversationResponse(
                text="I'm having trouble thinking right now. Please try again.",
                emotion="confused",
                should_speak=context.voice_mode
            )

        # Build contemplation request
        request_data = {
            "input": context.input_text,
            "user_id": context.user_id,
            "memories": memories,
            "world_state": world_state,
            "conversation_context": {
                "turn_number": context.turn_number,
                "voice_mode": context.voice_mode,
                "wake_word_triggered": context.wake_word_triggered
            }
        }

        try:
            async with self.http_session.post(
                f"{CONTEMPLATION_SERVICE_URL}/generate",
                json=request_data,
                timeout=aiohttp.ClientTimeout(total=180.0)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

                return ConversationResponse(
                    text=data.get('response', ''),
                    emotion=data.get('emotion'),
                    expression=data.get('expression'),
                    should_speak=context.voice_mode,
                    metadata=data.get('metadata', {})
                )

        except asyncio.TimeoutError:
            logger.error("Contemplation service timeout")
            return ConversationResponse(
                text="Sorry, I need a moment to think about that.",
                emotion="thoughtful",
                should_speak=context.voice_mode
            )

        except Exception as e:
            logger.error(f"Failed to generate response: {e}")
            return ConversationResponse(
                text="I encountered an error while thinking. Please try again.",
                emotion="confused",
                should_speak=context.voice_mode
            )

    async def _store_interaction(
        self,
        context: ConversationContext,
        response: ConversationResponse
    ):
        """Store interaction in memory service"""
        if not self.service_health['memory']:
            logger.warning("Memory service unavailable - skipping memory storage")
            return

        try:
            # Store user message
            await self.http_session.post(
                f"{MEMORY_SERVICE_URL}/store",
                json={
                    "interaction_type": "user_message",
                    "content": context.input_text,
                    "metadata": {
                        "user": context.user_id,
                        "source": context.input_source.value,
                        "timestamp": context.timestamp,
                        "conversation_id": context.conversation_id,
                        "turn": context.turn_number
                    }
                },
                timeout=aiohttp.ClientTimeout(total=10.0)
            )

            # Store assistant response
            await self.http_session.post(
                f"{MEMORY_SERVICE_URL}/store",
                json={
                    "interaction_type": "assistant_response",
                    "content": response.text,
                    "metadata": {
                        "user": context.user_id,
                        "emotion": response.emotion,
                        "timestamp": time.time(),
                        "conversation_id": context.conversation_id,
                        "turn": context.turn_number
                    }
                },
                timeout=aiohttp.ClientTimeout(total=10.0)
            )

            logger.debug(f"Stored interaction for conversation {context.conversation_id}")

        except Exception as e:
            logger.error(f"Failed to store interaction: {e}")

    async def _publish_response(
        self,
        context: ConversationContext,
        response: ConversationResponse
    ):
        """Publish response to all appropriate output channels"""
        try:
            # 1. Publish to chat output
            await self.mqtt_client.publish(
                TOPIC_CHAT_OUTPUT,
                json.dumps({
                    'text': response.text,
                    'user': context.user_id,
                    'timestamp': time.time(),
                    'conversation_id': context.conversation_id,
                    'turn': context.turn_number
                })
            )
            logger.debug(f"Published to {TOPIC_CHAT_OUTPUT}")

            # 2. Publish to avatar for text display
            await self.mqtt_client.publish(
                TOPIC_AVATAR_TEXT,
                json.dumps({
                    'text': response.text,
                    'duration': len(response.text) * 0.05  # ~50ms per character
                })
            )

            # 3. Publish emotion/expression to avatar
            if response.emotion or response.expression:
                await self.mqtt_client.publish(
                    TOPIC_AVATAR_EXPRESSION,
                    json.dumps({
                        'emotion': response.emotion or 'neutral',
                        'expression': response.expression or 'neutral',
                        'intensity': response.metadata.get('emotion_intensity', 0.7) if response.metadata else 0.7
                    })
                )
                logger.debug(f"Published expression: {response.emotion}")

            # 4. Publish to TTS if voice mode
            if response.should_speak:
                await self.mqtt_client.publish(
                    TOPIC_TTS_SPEAK,
                    json.dumps({
                        'text': response.text,
                        'emotion': response.emotion or 'neutral'
                    })
                )
                logger.debug("Published to TTS")

            logger.info(f"Response delivered to {context.user_id}: {response.text[:50]}...")

        except Exception as e:
            logger.error(f"Failed to publish response: {e}")

    async def _update_state(self, new_state: ConversationState):
        """Update conversation state in Redis and publish to MQTT"""
        old_state = self.state
        self.state = new_state

        # Store in Redis
        if self.redis_client:
            try:
                await self.redis_client.set(
                    CONVERSATION_STATE_KEY,
                    new_state.value
                )
            except Exception as e:
                logger.error(f"Failed to update state in Redis: {e}")

        # Publish state change
        try:
            await self.mqtt_client.publish(
                TOPIC_CONVERSATION_STATE,
                json.dumps({
                    'state': new_state.value,
                    'previous_state': old_state.value,
                    'timestamp': time.time()
                })
            )
        except Exception as e:
            logger.error(f"Failed to publish state change: {e}")

        logger.info(f"State: {old_state.value} → {new_state.value}")

    async def process_input(
        self,
        user_text: str,
        user_id: str = "unknown",
        source: InputSource = InputSource.TEXT_CHAT,
        voice_mode: bool = False,
        wake_word_triggered: bool = False
    ):
        """
        Main conversation processing pipeline

        Flow:
        1. Input received (text or voice)
        2. Get relevant memories from memory service
        3. Get world state from perception
        4. Call contemplation engine with context
        5. Get response with emotion and expression hints
        6. Publish to avatar (MQTT)
        7. Publish to TTS if voice mode
        8. Store interaction in memory
        9. Update conversation state
        """
        # Update state
        await self._update_state(ConversationState.PROCESSING)

        try:
            # Generate conversation context
            self.conversation_counter += 1
            if self.redis_client:
                try:
                    await self.redis_client.incr(TURN_COUNTER_KEY)
                except Exception:
                    pass

            conversation_id = f"conv_{user_id}_{int(time.time())}"

            context = ConversationContext(
                user_id=user_id,
                input_text=user_text,
                input_source=source,
                timestamp=time.time(),
                conversation_id=conversation_id,
                turn_number=self.conversation_counter,
                voice_mode=voice_mode,
                wake_word_triggered=wake_word_triggered
            )

            logger.info(f"Processing input from {user_id} ({source.value}): {user_text}")

            # Track conversation
            self.active_conversations[conversation_id] = context
            self.last_activity[user_id] = time.time()

            # Step 1: Get relevant memories
            memories = await self._get_relevant_memories(user_text, user_id)
            logger.debug(f"Retrieved {len(memories)} relevant memories")

            # Step 2: Get world state
            world_state = await self._get_world_state()
            logger.debug(f"World state: {len(world_state)} elements")

            # Step 3: Generate response
            await self._update_state(ConversationState.RESPONDING)
            response = await self._generate_response(context, memories, world_state)

            # Step 4: Publish response
            await self._publish_response(context, response)

            # Step 5: Store interaction
            await self._store_interaction(context, response)

            # Update state
            if voice_mode:
                await self._update_state(ConversationState.VOICE_MODE)
            else:
                await self._update_state(ConversationState.LISTENING)

            logger.info(f"Conversation turn {context.turn_number} complete")

        except Exception as e:
            logger.error(f"Error processing input: {e}", exc_info=True)
            await self._update_state(ConversationState.ERROR)

            # Send error response
            try:
                await self.mqtt_client.publish(
                    TOPIC_CHAT_OUTPUT,
                    json.dumps({
                        'text': "I encountered an error processing that. Please try again.",
                        'user': user_id,
                        'error': True
                    })
                )
            except Exception:
                pass

    async def handle_chat_input(self, message: Dict[str, Any]):
        """Handle text chat input from MQTT"""
        user_text = message.get('text', '').strip()
        user_id = message.get('user', 'unknown')

        if not user_text:
            logger.warning("Received empty chat message")
            return

        await self.process_input(
            user_text=user_text,
            user_id=user_id,
            source=InputSource.TEXT_CHAT,
            voice_mode=False
        )

    async def handle_wake_word(self, message: Dict[str, Any]):
        """Handle wake word detection"""
        confidence = message.get('confidence', 0.0)
        logger.info(f"Wake word detected (confidence: {confidence:.3f})")

        # Activate voice mode
        await self._update_state(ConversationState.VOICE_MODE)

        # Publish acknowledgment to avatar
        try:
            await self.mqtt_client.publish(
                TOPIC_AVATAR_EXPRESSION,
                json.dumps({
                    'emotion': 'attentive',
                    'expression': 'listening',
                    'intensity': 0.8
                })
            )
        except Exception as e:
            logger.error(f"Failed to publish wake acknowledgment: {e}")

    async def handle_voice_transcription(self, message: Dict[str, Any]):
        """Handle voice transcription from Whisper"""
        user_text = message.get('text', '').strip()
        user_id = message.get('user', 'unknown')
        confidence = message.get('confidence', 1.0)

        if not user_text:
            logger.warning("Received empty voice transcription")
            return

        logger.info(f"Voice transcription (confidence: {confidence:.2f}): {user_text}")

        await self.process_input(
            user_text=user_text,
            user_id=user_id,
            source=InputSource.VOICE_TRANSCRIPTION,
            voice_mode=True,
            wake_word_triggered=(self.state == ConversationState.VOICE_MODE)
        )

    async def mqtt_listener(self):
        """Main MQTT message listener loop"""
        logger.info("Starting MQTT listener...")

        try:
            async for message in self.mqtt_client.messages:
                if not self.running:
                    break

                topic = message.topic.value

                try:
                    # Parse payload
                    payload = message.payload.decode('utf-8')
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from {topic}: {message.payload}")
                    continue
                except Exception as e:
                    logger.error(f"Error parsing message from {topic}: {e}")
                    continue

                # Route to appropriate handler
                try:
                    if topic == TOPIC_CHAT_INPUT:
                        await self.handle_chat_input(data)

                    elif topic == TOPIC_WAKE_DETECTED:
                        await self.handle_wake_word(data)

                    elif topic == TOPIC_VOICE_TRANSCRIPTION:
                        await self.handle_voice_transcription(data)

                    else:
                        logger.debug(f"Unhandled topic: {topic}")

                except Exception as e:
                    logger.error(f"Error handling message from {topic}: {e}", exc_info=True)

        except MqttError as e:
            logger.error(f"MQTT listener error: {e}")
            raise

        finally:
            logger.info("MQTT listener stopped")

    async def idle_timeout_monitor(self):
        """Monitor for idle conversations and clean up"""
        logger.info("Starting idle timeout monitor...")

        while self.running:
            try:
                await asyncio.sleep(60.0)  # Check every minute

                current_time = time.time()

                # Check for idle conversations
                for user_id, last_time in list(self.last_activity.items()):
                    if current_time - last_time > INPUT_TIMEOUT:
                        logger.info(f"User {user_id} timed out after {INPUT_TIMEOUT}s")
                        del self.last_activity[user_id]

                        # If all conversations idle, return to IDLE state
                        if not self.last_activity and self.state != ConversationState.IDLE:
                            await self._update_state(ConversationState.IDLE)

                # Clean up old conversation contexts
                for conv_id in list(self.active_conversations.keys()):
                    context = self.active_conversations[conv_id]
                    if current_time - context.timestamp > INPUT_TIMEOUT:
                        del self.active_conversations[conv_id]

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in idle timeout monitor: {e}")

        logger.info("Idle timeout monitor stopped")

    async def health_monitor(self):
        """Periodic health check of dependent services"""
        logger.info("Starting health monitor...")

        while self.running:
            try:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL)
                await self._check_service_health()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in health monitor: {e}")

        logger.info("Health monitor stopped")

    async def start(self):
        """Start the conversation service"""
        logger.info("=" * 60)
        logger.info("CONVERSATION SERVICE STARTING")
        logger.info("=" * 60)

        await self.initialize()

        self.running = True
        await self._update_state(ConversationState.IDLE)

        # Start background tasks
        self.tasks.append(asyncio.create_task(self.mqtt_listener()))
        self.tasks.append(asyncio.create_task(self.idle_timeout_monitor()))
        self.tasks.append(asyncio.create_task(self.health_monitor()))

        logger.info("=" * 60)
        logger.info("CONVERSATION SERVICE OPERATIONAL")
        logger.info("=" * 60)

        # Wait for tasks
        try:
            await asyncio.gather(*self.tasks)
        except Exception as e:
            logger.error(f"Error in main task loop: {e}", exc_info=True)

    async def stop(self):
        """Gracefully stop the conversation service"""
        logger.info("=" * 60)
        logger.info("CONVERSATION SERVICE SHUTTING DOWN")
        logger.info("=" * 60)

        self.running = False

        # Cancel all background tasks
        for task in self.tasks:
            task.cancel()

        # Wait for tasks to complete
        await asyncio.gather(*self.tasks, return_exceptions=True)

        # Close MQTT connection
        if self.mqtt_client:
            try:
                await self.mqtt_client.__aexit__(None, None, None)
                logger.info("MQTT disconnected")
            except Exception as e:
                logger.error(f"Error disconnecting MQTT: {e}")

        # Close Redis connection
        if self.redis_client:
            try:
                await self.redis_client.close()
                logger.info("Redis disconnected")
            except Exception as e:
                logger.error(f"Error closing Redis: {e}")

        # Close HTTP session
        if self.http_session:
            try:
                await self.http_session.close()
                logger.info("HTTP session closed")
            except Exception as e:
                logger.error(f"Error closing HTTP session: {e}")

        logger.info("=" * 60)
        logger.info("CONVERSATION SERVICE SHUTDOWN COMPLETE")
        logger.info("=" * 60)


# Global instance for signal handlers
service: Optional[ConversationService] = None


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Received signal {signum}, initiating shutdown...")
    if service:
        asyncio.create_task(service.stop())


async def main():
    """Main entry point"""
    global service

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Create and start service
        service = ConversationService()
        await service.start()

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if service:
            await service.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Service terminated by user")
    except Exception as e:
        logger.error(f"Service crashed: {e}", exc_info=True)
        sys.exit(1)
