#!/usr/bin/env python3
"""
Conversation Orchestrator Service for Sentient Core v2
Production-ready orchestration of all dialogue components

Responsibilities:
- Subscribe to user input (MQTT: sentient/chat/input) - RE-ENABLED
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
- Full async MQTT subscriptions via SentientService base class
- Centralized configuration via sentient.config
- Canonical MQTT topics from sentient.common.mqtt_topics
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
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any
import random

try:
    import redis.asyncio as redis
except ImportError:
    redis = None

from sentient.config import get_config
from sentient.common.service_base import SentientService
from sentient.common.logging import setup_logging
from sentient.common import mqtt_topics
from sentient.services.conversation.system_tools import SystemTools


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


class ConversationOrchestrator(SentientService):
    """Production-ready conversation orchestration service"""

    def __init__(self):
        super().__init__(name="conversation")

        # Load centralized configuration
        self.cfg = get_config()

        # Service URLs from config
        self.memory_url = f"http://localhost:{self.cfg.memory.port}"
        self.contemplation_url = f"http://localhost:{self.cfg.contemplation.port}"
        self.perception_url = f"http://localhost:{self.cfg.perception.port}"

        # HTTP session for service calls
        self.http_session: Optional[aiohttp.ClientSession] = None

        # Redis for state management
        self.redis_client: Optional[redis.Redis] = None

        # State management
        self.state = ConversationState.IDLE
        self.active_conversations: Dict[str, ConversationContext] = {}
        self.last_activity: Dict[str, float] = {}

        # TTS preferences per conversation
        self.tts_preferences: Dict[str, bool] = {}

        # Service health tracking
        self.service_health: Dict[str, bool] = {
            'memory': False,
            'contemplation': False,
            'perception': False,
            'redis': False
        }

        # System tools for command execution
        self.system_tools = SystemTools()

        # Conversation history per user (for LLM context)
        self.conversation_histories: Dict[str, List[Dict[str, str]]] = {}

        # Circuit breaker state: tracks consecutive failures per service
        self._circuit_failures: Dict[str, int] = {
            'memory': 0,
            'contemplation': 0,
            'perception': 0,
        }
        self._circuit_open_until: Dict[str, float] = {
            'memory': 0.0,
            'contemplation': 0.0,
            'perception': 0.0,
        }
        self._circuit_max_failures = 3
        self._circuit_reset_seconds = 30.0

        # Background tasks
        self.background_tasks: List[asyncio.Task] = []

        # Conversation ID counter
        self.conversation_counter = 0

        # Redis keys
        self.CONVERSATION_STATE_KEY = "sentient:conversation:state"
        self.TURN_COUNTER_KEY = "sentient:conversation:turns"
        self.MOOD_KEY = "sentient:cortana:mood"

        # Core memory cache (facts about Jack)
        self._core_memory_cache = {}
        self._core_memory_last_fetch = 0.0
        self._core_memory_ttl = 300.0  # Refresh every 5 minutes

        # Rule-based emotion keywords (fast, no LLM call)
        self._emotion_keywords = {
            'joy': ['happy', 'glad', 'great', 'awesome', 'love', 'wonderful', 'fantastic', 'excellent', 'haha', 'lol', '😊', 'nice'],
            'curiosity': ['interesting', 'wonder', 'curious', 'hmm', 'fascinating', 'tell me', 'how does', 'what if'],
            'affection': ['care', 'appreciate', 'thank', 'grateful', 'miss you', 'proud', 'sweet', 'love you'],
            'sadness': ['sad', 'sorry', 'unfortunately', 'miss', 'lost', 'gone', 'hurt', 'lonely'],
            'anger': ['angry', 'annoyed', 'frustrated', 'damn', 'stupid', 'ridiculous', 'unacceptable'],
            'surprise': ['wow', 'whoa', 'unexpected', 'really?', 'no way', 'seriously', 'amazing'],
            'fear': ['worried', 'scared', 'anxious', 'nervous', 'concern', 'danger', 'threat', 'alarm'],
            'confidence': ['sure', 'absolutely', 'definitely', 'of course', 'easy', 'no problem', 'got it', 'done'],
            'playful': ['heh', 'tease', 'joke', 'funny', 'silly', 'sarcas', 'witty', 'cheeky'],
        }

        # Register MQTT handlers - CRITICAL: Re-enable chat input subscription
        @self.on_mqtt(mqtt_topics.CHAT_INPUT)
        async def handle_chat_input_mqtt(topic: str, payload: bytes):
            try:
                data = json.loads(payload.decode('utf-8'))
                await self.handle_chat_input(data)
            except Exception as e:
                self.logger.error(f"Error handling chat input: {e}", exc_info=True)

        @self.on_mqtt(mqtt_topics.WAKE_WORD_DETECTED)
        async def handle_wake_word_mqtt(topic: str, payload: bytes):
            try:
                data = json.loads(payload.decode('utf-8'))
                await self.handle_wake_word(data)
            except Exception as e:
                self.logger.error(f"Error handling wake word: {e}", exc_info=True)

        @self.on_mqtt(mqtt_topics.VOICE_TRANSCRIPTION)
        async def handle_voice_transcription_mqtt(topic: str, payload: bytes):
            try:
                data = json.loads(payload.decode('utf-8'))
                await self.handle_voice_transcription(data)
            except Exception as e:
                self.logger.error(f"Error handling voice transcription: {e}", exc_info=True)

        @self.on_mqtt(mqtt_topics.TTS_CONTROL)
        async def handle_tts_control_mqtt(topic: str, payload: bytes):
            try:
                data = json.loads(payload.decode('utf-8'))
                await self.handle_tts_control(data)
            except Exception as e:
                self.logger.error(f"Error handling TTS control: {e}", exc_info=True)

        @self.on_mqtt(mqtt_topics.FEEDBACK_RECEIVED)
        async def handle_feedback_mqtt(topic: str, payload: bytes):
            try:
                data = json.loads(payload.decode('utf-8'))
                await self._handle_feedback(data)
            except Exception as e:
                self.logger.error(f"Error handling feedback: {e}", exc_info=True)

    async def setup(self):
        """Initialize all service connections and resources"""
        self.logger.info("Initializing Conversation Orchestrator...")

        # Create log directory
        Path('/var/log/sentient').mkdir(parents=True, exist_ok=True)

        # Initialize HTTP session with connection limits
        self.http_session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=20, limit_per_host=10)
        )
        self.logger.info("HTTP session initialized")

        # Initialize Redis
        await self._initialize_redis()

        # Check service health with retries for startup race condition
        await self._check_service_health_with_retries()

        # Start background monitoring tasks
        self.background_tasks.append(
            asyncio.create_task(self._idle_timeout_monitor())
        )
        self.background_tasks.append(
            asyncio.create_task(self._health_monitor())
        )

        self.logger.info("Conversation Orchestrator initialization complete")

        # Schedule boot announcement (delayed to let all services settle)
        self.background_tasks.append(
            asyncio.create_task(self._boot_announcement())
        )

    async def _boot_announcement(self):
        """Announce Cortana is online after a brief startup delay"""
        try:
            await asyncio.sleep(5)  # Let services settle
            import random
            from datetime import datetime
            hour = datetime.now().hour

            if hour < 6:
                msgs = [
                    "Systems online. Late night mode active — I'm keeping watch.",
                    "Back online. Everything's quiet out there. I've got the night shift.",
                ]
            elif hour < 12:
                msgs = [
                    "Good morning. All systems online and running diagnostics now.",
                    "Morning, Jack. I'm up and running — all services green.",
                ]
            else:
                msgs = [
                    "All systems online. Ready when you are.",
                    "I'm back. Services are up and everything looks good.",
                    "Online and operational. All services reporting healthy.",
                ]

            text = random.choice(msgs)
            await self.mqtt_publish(mqtt_topics.CHAT_OUTPUT, {
                "text": text,
                "user": "proactive",
                "conversation_id": "boot",
                "proactive": True,
                "trigger_type": "boot",
                "emotion": {"emotion": "confident", "intensity": 0.6},
                "timestamp": time.time()
            })
            await self.mqtt_publish(mqtt_topics.AVATAR_EXPRESSION, {
                "emotion": "confident",
                "intensity": 0.6,
                "timestamp": time.time()
            })
            self.logger.info(f"Boot announcement: {text}")
        except Exception as e:
            self.logger.debug(f"Boot announcement skipped: {e}")

    async def teardown(self):
        """Cleanup service resources"""
        self.logger.info("Cleaning up Conversation Orchestrator...")

        # Cancel background tasks
        for task in self.background_tasks:
            task.cancel()
        await asyncio.gather(*self.background_tasks, return_exceptions=True)

        # Close Redis connection
        if self.redis_client:
            try:
                await self.redis_client.close()
                self.logger.info("Redis disconnected")
            except Exception as e:
                self.logger.error(f"Error closing Redis: {e}")

        # Close HTTP session
        if self.http_session:
            try:
                await self.http_session.close()
                self.logger.info("HTTP session closed")
            except Exception as e:
                self.logger.error(f"Error closing HTTP session: {e}")

    async def _initialize_redis(self):
        """Initialize Redis connection for state management"""
        if redis is None:
            self.logger.warning("Redis not available - using in-memory state only")
            self.service_health['redis'] = False
            return

        try:
            self.redis_client = redis.Redis(
                host=self.cfg.redis.host,
                port=self.cfg.redis.port,
                db=self.cfg.redis.db,
                decode_responses=True
            )

            # Test connection
            await self.redis_client.ping()
            self.service_health['redis'] = True
            self.logger.info(f"Connected to Redis at {self.cfg.redis.host}:{self.cfg.redis.port}")

            # Load conversation counter
            counter = await self.redis_client.get(self.TURN_COUNTER_KEY)
            if counter:
                self.conversation_counter = int(counter)

        except Exception as e:
            self.logger.error(f"Failed to connect to Redis: {e}")
            self.service_health['redis'] = False

    async def _check_service_health_with_retries(self, max_retries: int = 5):
        """Check service health with exponential backoff for startup race conditions."""
        for attempt in range(max_retries):
            await self._check_service_health()

            # Check if critical services are available
            if self.service_health['memory'] and self.service_health['contemplation']:
                self.logger.info("All critical services healthy")
                return

            if attempt < max_retries - 1:
                delay = 2 ** (attempt + 1)  # 2, 4, 8, 16, 32 seconds
                unhealthy = [
                    svc for svc, healthy in self.service_health.items()
                    if not healthy and svc in ('memory', 'contemplation')
                ]
                self.logger.warning(
                    f"Services not ready: {unhealthy}. "
                    f"Retry {attempt + 1}/{max_retries} in {delay}s..."
                )
                await asyncio.sleep(delay)

        # Log final state after all retries
        unhealthy = [svc for svc, healthy in self.service_health.items() if not healthy]
        if unhealthy:
            self.logger.warning(
                f"Some services still unavailable after {max_retries} retries: {unhealthy}. "
                "Will continue with periodic health monitoring."
            )

    def _circuit_is_open(self, service: str) -> bool:
        """Check if circuit breaker is open (service calls should be skipped)."""
        if time.time() < self._circuit_open_until.get(service, 0.0):
            return True
        return False

    def _circuit_record_success(self, service: str):
        """Record successful service call, reset circuit breaker."""
        self._circuit_failures[service] = 0
        self._circuit_open_until[service] = 0.0

    def _circuit_record_failure(self, service: str):
        """Record failed service call, potentially open circuit."""
        self._circuit_failures[service] = self._circuit_failures.get(service, 0) + 1
        if self._circuit_failures[service] >= self._circuit_max_failures:
            self._circuit_open_until[service] = time.time() + self._circuit_reset_seconds
            self.logger.warning(
                f"Circuit breaker OPEN for {service} after "
                f"{self._circuit_failures[service]} failures. "
                f"Skipping calls for {self._circuit_reset_seconds}s."
            )

    async def _check_service_health(self):
        """Check health of dependent services"""
        self.logger.info("Checking service health...")

        # Check Memory Service
        try:
            async with self.http_session.get(
                f"{self.memory_url}/health",
                timeout=aiohttp.ClientTimeout(total=5.0)
            ) as resp:
                if resp.status == 200:
                    self.service_health['memory'] = True
                    self.logger.info("Memory service: HEALTHY")
        except Exception as e:
            self.logger.warning(f"Memory service unavailable: {e}")
            self.service_health['memory'] = False

        # Check Contemplation Service
        try:
            async with self.http_session.get(
                f"{self.contemplation_url}/health",
                timeout=aiohttp.ClientTimeout(total=5.0)
            ) as resp:
                if resp.status == 200:
                    self.service_health['contemplation'] = True
                    self.logger.info("Contemplation service: HEALTHY")
        except Exception as e:
            self.logger.warning(f"Contemplation service unavailable: {e}")
            self.service_health['contemplation'] = False

        # Check Perception Service
        try:
            async with self.http_session.get(
                f"{self.perception_url}/health",
                timeout=aiohttp.ClientTimeout(total=5.0)
            ) as resp:
                if resp.status == 200:
                    self.service_health['perception'] = True
                    self.logger.info("Perception service: HEALTHY")
        except Exception as e:
            self.logger.warning(f"Perception service unavailable: {e}")
            self.service_health['perception'] = False

    async def _get_relevant_memories(self, query: str, user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Retrieve relevant memories from memory service"""
        if not self.service_health['memory'] or self._circuit_is_open('memory'):
            return []

        try:
            async with self.http_session.post(
                f"{self.memory_url}/recall",
                json={
                    "query": query,
                    "limit": limit,
                    "min_similarity": 0.3
                },
                timeout=aiohttp.ClientTimeout(total=30.0)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                self._circuit_record_success('memory')
                return data.get('memories', [])

        except Exception as e:
            self.logger.error(f"Failed to retrieve memories: {e}")
            self._circuit_record_failure('memory')
            return []

    async def _get_world_state(self) -> Dict[str, Any]:
        """Get current world state from perception service"""
        if not self.service_health['perception']:
            self.logger.warning("Perception service unavailable - using empty world state")
            return {}

        try:
            async with self.http_session.get(
                f"{self.perception_url}/state",
                timeout=aiohttp.ClientTimeout(total=5.0)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data.get('state', {})

        except Exception as e:
            self.logger.error(f"Failed to retrieve world state: {e}")
            return {}

    # Compiled regex patterns for deep contemplation detection
    _DEEP_PATTERNS = re.compile(
        r"i(?:'m| am) (?:stressed|worried|sad|lonely|scared|struggling)|"
        r"i feel\b|"
        r"how should i\b|what should i do\b|am i doing the right|"
        r"what do you think about\b|i need advice\b|help me understand\b|"
        r"meaning of\b|what is consciousness\b|do you think\b|"
        r"what'?s the point\b|why do we\b|purpose of life\b|what matters\b|"
        r"tell me about yourself\b|what do you really think\b|"
        r"be honest with me\b|your honest opinion\b|what are you feeling\b|"
        r"do you care about me\b|are we friends\b|what am i to you\b|do you love\b",
        re.IGNORECASE
    )

    def _should_deep_contemplate(self, user_text: str) -> bool:
        """Return True if the query warrants full multi-voice contemplation."""
        return bool(self._DEEP_PATTERNS.search(user_text))

    async def _generate_response(
        self,
        context: ConversationContext,
        memories: List[Dict[str, Any]],
        world_state: Dict[str, Any],
        history: Optional[List[Dict[str, str]]] = None,
        system_context: Optional[str] = None
    ) -> ConversationResponse:
        """Generate response using contemplation service"""
        if not self.service_health['contemplation'] or self._circuit_is_open('contemplation'):
            self.logger.error("Contemplation service unavailable - cannot generate response")
            tts_enabled = any(self.tts_preferences.values())
            return ConversationResponse(
                text="I'm having trouble thinking right now. Please try again.",
                emotion="confused",
                should_speak=context.voice_mode or tts_enabled
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
            },
            "history": history or [],
            "system_context": system_context or ""
        }

        try:
            async with self.http_session.post(
                f"{self.contemplation_url}/generate",
                json=request_data,
                timeout=aiohttp.ClientTimeout(total=180.0)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

                self._circuit_record_success('contemplation')

                # Check TTS preference for this conversation
                tts_enabled = any(self.tts_preferences.values())
                should_speak = context.voice_mode or tts_enabled

                return ConversationResponse(
                    text=data.get('response', ''),
                    emotion=data.get('emotion'),
                    expression=data.get('expression'),
                    should_speak=should_speak,
                    metadata=data.get('metadata', {})
                )

        except asyncio.TimeoutError:
            self.logger.error("Contemplation service timeout")
            self._circuit_record_failure('contemplation')
            tts_enabled = any(self.tts_preferences.values())
            return ConversationResponse(
                text="Sorry, I need a moment to think about that.",
                emotion="thoughtful",
                should_speak=context.voice_mode or tts_enabled
            )

        except Exception as e:
            self.logger.error(f"Failed to generate response: {e}")
            self._circuit_record_failure('contemplation')
            tts_enabled = any(self.tts_preferences.values())
            return ConversationResponse(
                text="I encountered an error while thinking. Please try again.",
                emotion="confused",
                should_speak=context.voice_mode or tts_enabled
            )

    async def _generate_response_stream(
        self,
        context: ConversationContext,
        memories: List[Dict[str, Any]],
        world_state: Dict[str, Any],
        history: Optional[List[Dict[str, str]]] = None,
        system_context: Optional[str] = None
    ) -> ConversationResponse:
        """Generate response using streaming contemplation, publishing partial tokens."""
        if not self.service_health['contemplation'] or self._circuit_is_open('contemplation'):
            tts_enabled = any(self.tts_preferences.values())
            return ConversationResponse(
                text="I'm having trouble thinking right now. Please try again.",
                emotion="confused",
                should_speak=context.voice_mode or tts_enabled
            )

        request_data = {
            "input": context.input_text,
            "user_id": context.user_id,
            "memories": memories,
            "world_state": world_state,
            "conversation_context": {
                "turn_number": context.turn_number,
                "voice_mode": context.voice_mode,
                "wake_word_triggered": context.wake_word_triggered
            },
            "history": history or [],
            "system_context": system_context
        }

        try:
            full_text = []
            async with self.http_session.post(
                f"{self.contemplation_url}/generate/stream",
                json=request_data,
                timeout=aiohttp.ClientTimeout(total=180.0)
            ) as resp:
                resp.raise_for_status()
                async for line in resp.content:
                    line_str = line.decode().strip()
                    if not line_str.startswith("data: "):
                        continue
                    try:
                        data = json.loads(line_str[6:])
                        token = data.get("token", "")
                        done = data.get("done", False)
                        if token:
                            full_text.append(token)
                            # Publish partial token for progressive display
                            await self.mqtt_publish(
                                mqtt_topics.CHAT_STREAM,
                                {
                                    "token": token,
                                    "partial": "".join(full_text),
                                    "done": False,
                                    "conversation_id": context.conversation_id,
                                    "timestamp": time.time()
                                }
                            )
                        if done:
                            break
                    except json.JSONDecodeError:
                        continue

            complete_text = "".join(full_text).strip()
            if not complete_text:
                complete_text = "I need a moment to gather my thoughts."

            # Enforce brevity — small models ignore prompt length rules
            complete_text = self._enforce_brevity(complete_text)

            self._circuit_record_success('contemplation')

            # Check TTS preference for this conversation
            tts_enabled = any(self.tts_preferences.values())
            should_speak = context.voice_mode or tts_enabled

            # Detect emotion from response text (rule-based, fast)
            detected_emotion = self._detect_emotion_from_text(complete_text)

            return ConversationResponse(
                text=complete_text,
                emotion=detected_emotion,
                should_speak=should_speak
            )

        except asyncio.TimeoutError:
            self.logger.error("Streaming contemplation timeout")
            self._circuit_record_failure('contemplation')
            tts_enabled = any(self.tts_preferences.values())
            return ConversationResponse(
                text="Sorry, I need a moment to think about that.",
                emotion="thoughtful",
                should_speak=context.voice_mode or tts_enabled
            )
        except Exception as e:
            self.logger.error(f"Failed to stream response: {e}")
            self._circuit_record_failure('contemplation')
            # Fall back to non-streaming
            return await self._generate_response(context, memories, world_state, history, system_context=system_context)

    async def _store_interaction(
        self,
        context: ConversationContext,
        response: ConversationResponse,
        original_text: Optional[str] = None
    ):
        """Store interaction in memory service"""
        if not self.service_health['memory']:
            self.logger.warning("Memory service unavailable - skipping memory storage")
            return

        try:
            async with self.http_session.post(
                f"{self.memory_url}/store",
                json={
                    "user_msg": original_text or context.input_text,
                    "assistant_msg": response.text,
                    "force_episodic": False
                },
                timeout=aiohttp.ClientTimeout(total=10.0)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.logger.debug(
                        f"Stored interaction for conversation {context.conversation_id} "
                        f"(importance: {data.get('importance_score', 'unknown')})"
                    )
                else:
                    text = await resp.text()
                    self.logger.error(f"Memory store failed ({resp.status}): {text}")

        except Exception as e:
            self.logger.error(f"Failed to store interaction: {e}")

    def _generate_suggestions(self, user_text: str, response_text: str, tool_context: str = None) -> list:
        """Generate 2-3 follow-up suggestion chips based on response context."""
        import random
        suggestions = []
        response_lower = response_text.lower()

        # System/technical context
        if tool_context:
            tc_lower = tool_context.lower()
            if 'gpu' in tc_lower or 'temperature' in tc_lower:
                suggestions.append("How's the GPU doing now?")
            if 'service' in tc_lower:
                suggestions.append("Run a full diagnostic")
            if 'disk' in tc_lower:
                suggestions.append("Can you clean up some space?")
            if 'network' in tc_lower:
                suggestions.append("Scan the network")

        # Emotional/personal context
        if any(w in response_lower for w in ['feel', 'emotion', 'mood']):
            suggestions.append("Tell me more about that")
            suggestions.append("How are you feeling right now?")

        # Memory/knowledge context
        if any(w in response_lower for w in ['remember', 'recall', 'last time']):
            suggestions.append("What else do you remember?")
            suggestions.append("Search your memories")

        # Advice context
        if any(w in response_lower for w in ['suggest', 'recommend', 'should', 'could try']):
            suggestions.append("Tell me more")
            suggestions.append("What else could I try?")

        # Weather/environment
        if any(w in response_lower for w in ['weather', 'temperature', 'rain', 'sunny']):
            suggestions.append("What about tomorrow?")

        # Generic fallbacks if nothing specific matched
        if len(suggestions) < 2:
            generic = [
                "Tell me something interesting",
                "How are you doing?",
                "What's on your mind?",
                "Run a system check",
                "What do you remember about me?"
            ]
            while len(suggestions) < 2:
                pick = random.choice(generic)
                if pick not in suggestions:
                    suggestions.append(pick)

        return suggestions[:3]

    async def _publish_response(
        self,
        context: ConversationContext,
        response: ConversationResponse,
        suggestions: list = None
    ):
        """Publish response to all appropriate output channels"""
        try:
            # 1. Publish to chat output
            chat_payload = {
                'text': response.text,
                'user': context.user_id,
                'timestamp': time.time(),
                'conversation_id': context.conversation_id,
                'turn': context.turn_number
            }
            if suggestions:
                chat_payload['suggestions'] = suggestions
            await self.mqtt_publish(
                mqtt_topics.CHAT_OUTPUT,
                chat_payload
            )
            self.logger.debug(f"Published to {mqtt_topics.CHAT_OUTPUT}")

            # 2. Publish to avatar for text display
            await self.mqtt_publish(
                mqtt_topics.AVATAR_TEXT,
                {
                    'text': response.text,
                    'duration': len(response.text) * 0.05  # ~50ms per character
                }
            )

            # 3. Publish emotion/expression to avatar
            if response.emotion or response.expression:
                await self.mqtt_publish(
                    mqtt_topics.AVATAR_EXPRESSION,
                    {
                        'emotion': response.emotion or 'neutral',
                        'expression': response.expression or 'neutral',
                        'intensity': response.metadata.get('emotion_intensity', 0.7) if response.metadata else 0.7
                    }
                )
                self.logger.debug(f"Published expression: {response.emotion}")

            # 4. Publish to TTS if voice mode
            if response.should_speak:
                await self.mqtt_publish(
                    mqtt_topics.TTS_SPEAK,
                    {
                        'text': response.text,
                        'emotion': response.emotion or 'neutral'
                    }
                )
                self.logger.debug("Published to TTS")

            self.logger.info(f"Response delivered to {context.user_id}: {response.text[:50]}...")

        except Exception as e:
            self.logger.error(f"Failed to publish response: {e}")

    async def _update_state(self, new_state: ConversationState):
        """Update conversation state in Redis and publish to MQTT"""
        old_state = self.state
        self.state = new_state

        # Store in Redis
        if self.redis_client:
            try:
                await self.redis_client.set(
                    self.CONVERSATION_STATE_KEY,
                    new_state.value
                )
            except Exception as e:
                self.logger.error(f"Failed to update state in Redis: {e}")

        # Publish state change
        try:
            await self.mqtt_publish(
                mqtt_topics.CONVERSATION_STATE,
                {
                    'state': new_state.value,
                    'previous_state': old_state.value,
                    'timestamp': time.time()
                }
            )
        except Exception as e:
            self.logger.error(f"Failed to publish state change: {e}")

        self.logger.info(f"State: {old_state.value} → {new_state.value}")

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
                    await self.redis_client.incr(self.TURN_COUNTER_KEY)
                    # Update last interaction time for proactive service
                    await self.redis_client.set("interaction:last_timestamp", str(time.time()))
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

            self.logger.info(f"Processing input from {user_id} ({source.value}): {user_text}")

            # Track conversation
            # Bound active conversations to prevent memory leaks
            if len(self.active_conversations) >= 100:
                # Remove oldest conversation
                oldest_id = min(self.active_conversations, key=lambda k: self.active_conversations[k].timestamp)
                del self.active_conversations[oldest_id]

            self.active_conversations[conversation_id] = context
            self.last_activity[user_id] = time.time()

            # Step 0: Get Cortana's current mood
            mood = await self._get_mood()
            mood_context = self._mood_to_context(mood)

            # Step 0.5: Get core memory (facts about Jack)
            core_memory = await self._get_core_memory()
            core_facts = self._format_core_facts(core_memory)

            # Step 1: Get relevant memories
            memories = await self._get_relevant_memories(user_text, user_id)
            self.logger.debug(f"Retrieved {len(memories)} relevant memories")

            # Step 2: Get world state
            world_state = await self._get_world_state()
            self.logger.debug(f"World state: {len(world_state)} elements")

            # Step 2.5: Check if user is requesting a system operation
            tool_context = None
            # Check for command chains first (multi-step) — chains take priority
            chain_result = self.system_tools.detect_chain(user_text)
            if chain_result:
                chain_name, chain_def = chain_result
                self.logger.info(f"Executing command chain: {chain_name}")
                tool_output = await self.system_tools.execute_chain(chain_name, chain_def, user_text)
                tool_context = self.system_tools.format_for_prompt(
                    f"chain/{chain_name}", tool_output
                )
            else:
                intent = self.system_tools.detect_intent(user_text)
                if intent:
                    category, command = intent
                    self.logger.info(f"System tool detected: {category}/{command}")
                    tool_output = await self.system_tools.execute(category, command, user_text)
                    tool_context = self.system_tools.format_for_prompt(
                        f"{category}/{command}", tool_output
                    )

            # Step 0.6: Get feedback context (user ratings of past responses)
            feedback_context = await self._get_feedback_context(user_id)

            # Build system_context for contemplation (separate from user text)
            # This goes into the system prompt, keeping user message clean
            system_context_parts = []
            if core_facts:
                system_context_parts.append(core_facts)
            if mood_context:
                system_context_parts.append(mood_context)
            if feedback_context:
                system_context_parts.append(feedback_context)
            if tool_context:
                system_context_parts.append(f"System data (use this to answer Jack's question):\n{tool_context}")
            system_context = "\n\n".join(system_context_parts) if system_context_parts else None

            # Get conversation history for this user
            if user_id not in self.conversation_histories:
                self.conversation_histories[user_id] = []
            user_history = self.conversation_histories[user_id]

            # Add current user message to history (clean, original text)
            user_history.append({"role": "user", "content": user_text})

            # Step 2.5: Determine contemplation depth
            deep_contemplate = self._should_deep_contemplate(user_text)

            # Step 3: Generate response
            response_start = time.time()
            await self._update_state(ConversationState.RESPONDING)
            if deep_contemplate:
                self.logger.info("Deep contemplation mode activated")
                await self.mqtt_publish(mqtt_topics.PERSONA_STATE, {
                    'state': 'deep_contemplating',
                    'timestamp': datetime.now().isoformat()
                })
                response = await self._generate_response(
                    context, memories, world_state,
                    history=user_history[:-1],
                    system_context=system_context
                )
            else:
                response = await self._generate_response_stream(
                    context, memories, world_state,
                    history=user_history[:-1],  # Pass history before current msg
                    system_context=system_context
                )

            # Append assistant response to history
            user_history.append({"role": "assistant", "content": response.text})

            # Trim history to last 20 messages (10 turns)
            if len(user_history) > 20:
                self.conversation_histories[user_id] = user_history[-20:]

            # Step 3.5: Generate follow-up suggestions
            suggestions = self._generate_suggestions(user_text, response.text, tool_context)

            # Step 4: Publish response
            await self._publish_response(context, response, suggestions=suggestions)

            # Step 4.5: Update mood based on response emotion
            detected_emotion = response.emotion or self._detect_emotion_from_text(response.text)
            await self._update_mood(detected_emotion, response.text)

            # Step 5: Store interaction (use original user_text, not mood/tool-augmented version)
            await self._store_interaction(context, response, original_text=user_text)

            # Step 5.5: Auto-extract facts from user's message
            await self._auto_extract_facts(user_text, user_id)

            # Step 6: Update conversation stats
            response_time_ms = (time.time() - response_start) * 1000
            await self._update_conversation_stats(user_id, response_time_ms)

            # Update state
            if voice_mode:
                await self._update_state(ConversationState.VOICE_MODE)
            else:
                await self._update_state(ConversationState.LISTENING)

            self.logger.info(f"Conversation turn {context.turn_number} complete")

        except Exception as e:
            self.logger.error(f"Error processing input: {e}", exc_info=True)
            await self._update_state(ConversationState.ERROR)

            # Send error response
            try:
                await self.mqtt_publish(
                    mqtt_topics.CHAT_OUTPUT,
                    {
                        'text': "I encountered an error processing that. Please try again.",
                        'user': user_id,
                        'error': True
                    }
                )
            except Exception:
                pass

    @staticmethod
    def _enforce_brevity(text: str, max_sentences: int = 2) -> str:
        """Enforce sentence limit — small models ignore prompt length rules.

        Skips leading greeting/filler sentences to keep substantive content.
        """
        if not text:
            return text
        parts = re.split(r'(?<=[.!?])\s+', text.strip())
        if len(parts) <= max_sentences:
            return text.strip()

        # Skip leading greeting/filler if there are enough sentences
        greetings = ('hey', 'hello', 'hi ', 'greetings', 'good morning',
                     'good evening', 'good afternoon', "what's up")
        start = 0
        if len(parts) > max_sentences and parts[0].lower().startswith(greetings):
            start = 1

        result = ' '.join(parts[start:start + max_sentences])
        if result and result[-1] not in '.!?':
            result += '.'
        return result

    def _detect_emotion_from_text(self, text: str) -> str:
        """Fast rule-based emotion detection from response text. No LLM needed."""
        text_lower = text.lower()
        scores = {}
        for emotion, keywords in self._emotion_keywords.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > 0:
                scores[emotion] = score

        if not scores:
            return 'neutral'
        return max(scores, key=scores.get)

    async def _get_mood(self) -> Dict[str, Any]:
        """Read Cortana's current mood from Redis, with time-decay toward neutral."""
        default_mood = {
            'emotion': 'neutral',
            'valence': 0.0,
            'intensity': 0.3,
            'last_updated': time.time(),
            'recent': []
        }

        if not self.redis_client:
            return default_mood

        try:
            raw = await self.redis_client.get(self.MOOD_KEY)
            if not raw:
                return default_mood

            mood = json.loads(raw)

            # Time-decay: intensity fades toward 0.3 (baseline) over 30 minutes
            elapsed = time.time() - mood.get('last_updated', time.time())
            decay_factor = max(0.0, 1.0 - (elapsed / 1800.0))  # Full decay over 30 min
            mood['intensity'] = 0.3 + (mood.get('intensity', 0.3) - 0.3) * decay_factor
            mood['valence'] = mood.get('valence', 0.0) * decay_factor

            # If intensity has decayed to baseline, emotion resets to neutral
            if mood['intensity'] <= 0.35:
                mood['emotion'] = 'neutral'
                mood['valence'] = 0.0

            return mood

        except Exception as e:
            self.logger.debug(f"Failed to read mood from Redis: {e}")
            return default_mood

    async def _update_mood(self, detected_emotion: str, response_text: str):
        """Update Cortana's mood based on detected emotion from her response."""
        if not self.redis_client:
            return

        # Emotion valence mapping
        valence_map = {
            'joy': 0.8, 'affection': 0.7, 'playful': 0.6, 'confidence': 0.5,
            'curiosity': 0.3, 'surprise': 0.2, 'neutral': 0.0,
            'sadness': -0.5, 'anger': -0.6, 'fear': -0.4,
        }

        try:
            current = await self._get_mood()

            new_valence = valence_map.get(detected_emotion, 0.0)
            # Blend: 60% new emotion, 40% lingering mood
            blended_valence = new_valence * 0.6 + current.get('valence', 0.0) * 0.4

            # Intensity rises with strong emotions, stays moderate for neutral
            new_intensity = 0.7 if detected_emotion != 'neutral' else 0.3
            blended_intensity = new_intensity * 0.6 + current.get('intensity', 0.3) * 0.4

            # Track recent emotions (last 5)
            recent = current.get('recent', [])
            recent.append(detected_emotion)
            if len(recent) > 5:
                recent = recent[-5:]

            mood = {
                'emotion': detected_emotion,
                'valence': round(blended_valence, 3),
                'intensity': round(blended_intensity, 3),
                'last_updated': time.time(),
                'recent': recent
            }

            await self.redis_client.set(self.MOOD_KEY, json.dumps(mood))
            self.logger.debug(f"Mood updated: {detected_emotion} (valence={mood['valence']}, intensity={mood['intensity']})")

        except Exception as e:
            self.logger.debug(f"Failed to update mood in Redis: {e}")

    def _mood_to_context(self, mood: Dict[str, Any]) -> str:
        """Convert mood state to a natural language context string for the LLM."""
        emotion = mood.get('emotion', 'neutral')
        intensity = mood.get('intensity', 0.3)
        recent = mood.get('recent', [])

        if emotion == 'neutral' and intensity <= 0.35:
            return ""  # No mood context needed when neutral

        # Intensity descriptors
        if intensity > 0.7:
            level = "strongly"
        elif intensity > 0.5:
            level = "somewhat"
        else:
            level = "slightly"

        # Natural mood descriptions
        mood_descriptions = {
            'joy': f"You're {level} upbeat right now",
            'curiosity': f"You're {level} intrigued by something",
            'affection': f"You're feeling {level} warm and caring",
            'sadness': f"You're {level} down right now",
            'anger': f"You're {level} irritated",
            'surprise': f"You're {level} caught off guard",
            'fear': f"You're {level} on edge",
            'confidence': f"You're feeling {level} self-assured",
            'playful': f"You're in a {level} playful mood",
        }

        desc = mood_descriptions.get(emotion, "")
        if not desc:
            return ""

        # Add trend if recent emotions show a pattern
        if len(recent) >= 3:
            unique_recent = set(recent[-3:])
            if len(unique_recent) == 1 and emotion != 'neutral':
                desc += " — this mood has been building"

        return f"[Current mood: {desc}.]"

    async def _get_core_memory(self) -> Dict[str, Any]:
        """Fetch core memory (facts about Jack) from memory service, with caching."""
        now = time.time()
        if self._core_memory_cache and (now - self._core_memory_last_fetch) < self._core_memory_ttl:
            return self._core_memory_cache

        if not self.service_health['memory'] or self._circuit_is_open('memory'):
            return self._core_memory_cache  # Return stale cache if service down

        try:
            async with self.http_session.get(
                f"{self.memory_url}/core",
                timeout=aiohttp.ClientTimeout(total=5.0)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                self._core_memory_cache = data.get('value', {})
                self._core_memory_last_fetch = now
                self.logger.debug(f"Core memory refreshed: {len(self._core_memory_cache)} facts")
                return self._core_memory_cache
        except Exception as e:
            self.logger.debug(f"Failed to fetch core memory: {e}")
            return self._core_memory_cache

    def _format_core_facts(self, core_memory: Dict[str, Any]) -> str:
        """Format core memory into a concise facts string for prompts."""
        if not core_memory:
            return ""

        facts = []
        for key, value in core_memory.items():
            if key == 'name':
                continue  # Already known as "Jack" in the prompt
            elif key.startswith('preferences.'):
                pref_name = key.replace('preferences.', '')
                if isinstance(value, list):
                    facts.append(f"Jack's {pref_name}: {', '.join(str(v) for v in value)}")
                else:
                    facts.append(f"Jack's {pref_name}: {value}")
            elif key.startswith('user_note_') or key.startswith('auto_interest_'):
                # User-submitted notes - rewrite from Jack's perspective
                note = str(value)
                if note.lower().startswith(('i ', "i'm ", 'my ')):
                    note = f"Jack said: \"{note}\""
                facts.append(note)
            else:
                facts.append(f"Jack's {key}: {value}")

        if not facts:
            return ""

        return "Facts about Jack: " + "; ".join(facts[:5])

    async def _auto_extract_facts(self, user_text: str, user_id: str):
        """Automatically extract and store facts from user messages using patterns."""
        text = user_text.lower().strip()

        patterns = [
            # "my name is X"
            (r"my name is (\w+)", "name"),
            # "I like X" / "I love X"
            (r"i (?:really )?(?:like|love|enjoy) (\w[\w\s]{1,30}?)(?:\.|,|!|$)", None),
            # "my favorite X is Y"
            (r"my fav(?:ou?rite)? (\w+) is ([\w\s]+?)(?:\.|,|!|$)", None),
            # "I work at/in X"
            (r"i work (?:at|in|for) ([\w\s]+?)(?:\.|,|!|$)", "work"),
            # "I live in X"
            (r"i live in ([\w\s]+?)(?:\.|,|!|$)", "location"),
            # "I'm a X" (profession)
            (r"i'?m a(?:n)? ([\w\s]+?)(?:\.|,|!|$)", "profession"),
            # "my X is Y" (general possessive)
            (r"my (\w+) is ([\w\s]+?)(?:\.|,|!|$)", None),
        ]

        for pattern, key_name in patterns:
            match = re.search(pattern, text)
            if not match:
                continue

            groups = match.groups()
            if key_name == "name":
                fact_key = "name"
                fact_value = groups[0].capitalize()
            elif key_name:
                fact_key = key_name
                fact_value = groups[0].strip()
            elif len(groups) == 2:
                # "my X is Y" or "favorite X is Y"
                fact_key = f"preferences.{groups[0].strip()}"
                fact_value = groups[1].strip()
            elif len(groups) == 1:
                # "I like X"
                fact_key = f"auto_interest_{int(time.time())}"
                fact_value = groups[0].strip()
            else:
                continue

            # Don't store very short or likely false matches
            if len(str(fact_value)) < 2 or fact_value in ('a', 'an', 'the', 'it', 'this', 'that'):
                continue

            try:
                # For auto interests, use rotating keys (0-19) to cap at 20
                if fact_key.startswith('auto_interest_'):
                    core = await self._get_core_memory()
                    interest_count = sum(1 for k in core if k.startswith('auto_interest_'))
                    fact_key = f"auto_interest_{interest_count % 20}"

                async with self.http_session.post(
                    f"{self.memory_url}/core",
                    json={"key": fact_key, "value": fact_value},
                    timeout=aiohttp.ClientTimeout(total=5.0)
                ) as resp:
                    if resp.status == 200:
                        self.logger.info(f"Auto-learned fact: {fact_key} = {fact_value}")
                        # Invalidate cache
                        self._core_memory_last_fetch = 0.0
            except Exception:
                pass

            break  # Only extract one fact per message

    async def _update_conversation_stats(self, user_id: str, response_time_ms: float):
        """Update conversation statistics in Redis."""
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            pipe = self.redis.pipeline()

            # Daily message count
            daily_key = f"stats:messages:{today}"
            pipe.incr(daily_key)
            pipe.expire(daily_key, 86400 * 7)  # Keep 7 days

            # Total message count (all time)
            pipe.incr("stats:messages:total")

            # Daily conversation count (unique user interactions per day)
            daily_users_key = f"stats:users:{today}"
            pipe.sadd(daily_users_key, user_id)
            pipe.expire(daily_users_key, 86400 * 7)

            # Response time tracking (rolling average)
            pipe.lpush("stats:response_times", str(int(response_time_ms)))
            pipe.ltrim("stats:response_times", 0, 99)  # Keep last 100

            # Hourly activity (which hours are most active)
            hour = datetime.now().hour
            pipe.hincrby("stats:hourly_activity", str(hour), 1)

            await pipe.execute()
        except Exception as e:
            self.logger.debug(f"Failed to update stats: {e}")

    async def get_conversation_stats(self) -> dict:
        """Get conversation statistics."""
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            pipe = self.redis.pipeline()
            pipe.get(f"stats:messages:{today}")
            pipe.get("stats:messages:total")
            pipe.scard(f"stats:users:{today}")
            pipe.lrange("stats:response_times", 0, 99)
            pipe.hgetall("stats:hourly_activity")
            results = await pipe.execute()

            # Calculate average response time
            times = [int(t) for t in (results[3] or []) if t]
            avg_time = sum(times) / len(times) if times else 0

            return {
                "messages_today": int(results[0] or 0),
                "messages_total": int(results[1] or 0),
                "unique_users_today": int(results[2] or 0),
                "avg_response_time_ms": round(avg_time),
                "hourly_activity": {k: int(v) for k, v in (results[4] or {}).items()}
            }
        except Exception as e:
            self.logger.debug(f"Failed to get stats: {e}")
            return {}

    async def handle_chat_input(self, message: Dict[str, Any]):
        """Handle text chat input from MQTT"""
        user_text = message.get('text', '').strip()
        user_id = message.get('user', 'unknown')

        if not user_text:
            self.logger.warning("Received empty chat message")
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
        self.logger.info(f"Wake word detected (confidence: {confidence:.3f})")

        # Activate voice mode
        await self._update_state(ConversationState.VOICE_MODE)

        # Publish acknowledgment to avatar
        try:
            await self.mqtt_publish(
                mqtt_topics.AVATAR_EXPRESSION,
                {
                    'emotion': 'attentive',
                    'expression': 'listening',
                    'intensity': 0.8
                }
            )
        except Exception as e:
            self.logger.error(f"Failed to publish wake acknowledgment: {e}")

    async def handle_voice_transcription(self, message: Dict[str, Any]):
        """Handle voice transcription from Whisper"""
        user_text = message.get('text', '').strip()
        user_id = message.get('user', 'unknown')
        confidence = message.get('confidence', 1.0)

        if not user_text:
            self.logger.warning("Received empty voice transcription")
            return

        self.logger.info(f"Voice transcription (confidence: {confidence:.2f}): {user_text}")

        await self.process_input(
            user_text=user_text,
            user_id=user_id,
            source=InputSource.VOICE_TRANSCRIPTION,
            voice_mode=True,
            wake_word_triggered=(self.state == ConversationState.VOICE_MODE)
        )

    async def handle_tts_control(self, message: dict):
        """Handle TTS toggle control from web UI or other sources"""
        enabled = message.get('enabled', False)
        source = message.get('source', 'unknown')
        self.tts_preferences[source] = enabled
        self.logger.info(f"TTS {'enabled' if enabled else 'disabled'} by {source}")

    async def _handle_feedback(self, payload: dict):
        """Store user thumbs up/down feedback in Redis."""
        if not self.redis_client:
            return
        user_id = payload.get('user_id', 'web_user')
        try:
            key = f"feedback:{user_id}"
            await self.redis_client.lpush(key, json.dumps(payload))
            await self.redis_client.ltrim(key, 0, 19)  # Keep last 20 items
            fb_type = payload.get('feedback', '?')
            self.logger.info(f"Feedback stored for {user_id}: {fb_type}")
        except Exception as e:
            self.logger.error(f"Failed to store feedback: {e}")

    async def _get_feedback_context(self, user_id: str) -> str:
        """Build a feedback context string from recent user ratings."""
        if not self.redis_client:
            return ""
        try:
            key = f"feedback:{user_id}"
            items = await self.redis_client.lrange(key, 0, 9)
            if not items:
                return ""
            ups = 0
            downs = 0
            for raw in items:
                try:
                    entry = json.loads(raw)
                    if entry.get('feedback') == 'up':
                        ups += 1
                    elif entry.get('feedback') == 'down':
                        downs += 1
                except Exception:
                    pass
            if ups == 0 and downs == 0:
                return ""
            parts = []
            if ups > 0:
                parts.append(f"{ups} positive")
            if downs > 0:
                parts.append(f"{downs} negative")
            return f"[Response preferences: {', '.join(parts)} ratings from Jack recently.]"
        except Exception as e:
            self.logger.debug(f"Failed to read feedback context: {e}")
            return ""

    async def _idle_timeout_monitor(self):
        """Monitor for idle conversations and clean up"""
        self.logger.info("Starting idle timeout monitor...")

        while self._running:
            try:
                await asyncio.sleep(60.0)  # Check every minute

                current_time = time.time()
                idle_timeout = self.cfg.conversation.idle_timeout

                # Check for idle conversations
                for user_id, last_time in list(self.last_activity.items()):
                    if current_time - last_time > idle_timeout:
                        self.logger.info(f"User {user_id} timed out after {idle_timeout}s")
                        del self.last_activity[user_id]

                        # If all conversations idle, return to IDLE state
                        if not self.last_activity and self.state != ConversationState.IDLE:
                            await self._update_state(ConversationState.IDLE)

                # Clean up old conversation contexts
                for conv_id in list(self.active_conversations.keys()):
                    context = self.active_conversations[conv_id]
                    if current_time - context.timestamp > idle_timeout:
                        del self.active_conversations[conv_id]

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in idle timeout monitor: {e}")

        self.logger.info("Idle timeout monitor stopped")

    async def _health_monitor(self):
        """Periodic health check of dependent services"""
        self.logger.info("Starting health monitor...")

        while self._running:
            try:
                await asyncio.sleep(120.0)  # Check every 2 minutes
                await self._check_service_health()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in health monitor: {e}")

        self.logger.info("Health monitor stopped")


if __name__ == "__main__":
    """Entry point for running the conversation orchestrator service"""
    import sys

    orchestrator = ConversationOrchestrator()

    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        orchestrator.logger.info("Service terminated by user")
    except Exception as e:
        orchestrator.logger.error(f"Service crashed: {e}", exc_info=True)
        sys.exit(1)
