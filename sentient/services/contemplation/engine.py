#!/usr/bin/env python3
"""
Contemplative Reasoning Engine - Multi-Voice Internal Dialogue System

The cognitive heart of Cortana's consciousness. Implements genuine contemplation
through five internal voices that process input in parallel, synthesizing
perspectives into authentic, emotionally-aware responses.

Voices:
    OBSERVER: Raw factual observations ("I notice...", "I see...")
    ANALYST: Logical implications ("This suggests...", "So...")
    EMPATH: Emotional resonance ("I feel...", "That must be...")
    SKEPTIC: Alternative interpretations ("But what if...", "Or maybe...")
    MEMORY: Past context connections ("I remember...", "Last time...")
"""

import asyncio
import json
import os
import random
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

import aiohttp
import paho.mqtt.client as mqtt

from sentient.config import get_config
from sentient.common.logging import setup_logging
from sentient.common import mqtt_topics

# Logger
logger = setup_logging("contemplation")


class Voice(Enum):
    """The five internal voices of contemplation"""
    OBSERVER = "observer"
    ANALYST = "analyst"
    EMPATH = "empath"
    SKEPTIC = "skeptic"
    MEMORY = "memory"


class InputType(Enum):
    """Types of input that trigger contemplation"""
    VOICE = "voice"
    TEXT = "text"
    SENSOR = "sensor"
    INTERNAL = "internal"


class EmotionCategory(Enum):
    """Primary emotion categories"""
    JOY = "joy"
    SADNESS = "sadness"
    ANGER = "anger"
    FEAR = "fear"
    SURPRISE = "surprise"
    DISGUST = "disgust"
    CURIOSITY = "curiosity"
    AFFECTION = "affection"
    NEUTRAL = "neutral"


@dataclass
class EmotionState:
    """Current emotional state derived from contemplation"""
    primary: EmotionCategory = EmotionCategory.NEUTRAL
    valence: float = 0.0       # -1 (negative) to 1 (positive)
    arousal: float = 0.3       # 0 (calm) to 1 (excited)
    intensity: float = 0.3     # 0 (mild) to 1 (strong)
    confidence: float = 0.5    # How certain we are about this emotion


@dataclass
class VoicePerspective:
    """Single voice's contribution to contemplation"""
    voice: Voice
    content: str
    generation_time_ms: float
    raw_llm_response: str = ""


@dataclass
class ExpressionHints:
    """Physical expression cues extracted from response"""
    gestures: List[str] = field(default_factory=list)  # *tilts head*, *smiles*
    tone: str = "neutral"                               # warm, concerned, playful
    pacing: str = "normal"                              # slow, normal, animated


@dataclass
class ContemplationResult:
    """Complete result of a contemplation cycle"""
    input_text: str
    input_type: InputType
    perspectives: Dict[str, VoicePerspective]
    synthesized_response: str
    hesitations_added: str
    emotion: EmotionState
    expression: ExpressionHints
    total_time_ms: float
    timestamp: datetime = field(default_factory=datetime.now)


# Voice-specific prompts that shape each perspective
VOICE_PROMPTS = {
    Voice.OBSERVER: """You are the OBSERVER voice in a contemplative mind. Your role is to notice raw facts without interpretation.

Rules:
- Start with "I notice..." or "I see..." or "I observe..."
- State only what is directly perceivable
- No judgments, interpretations, or emotional reactions
- Be precise and concrete
- 1-2 sentences maximum

Input to observe: {input}

Your observation:""",

    Voice.ANALYST: """You are the ANALYST voice in a contemplative mind. Your role is to identify logical implications and patterns.

Rules:
- Start with "This suggests..." or "So..." or "Logically..."
- Draw reasoned conclusions from facts
- Identify patterns, causes, or consequences
- Stay logical, not emotional
- 1-2 sentences maximum

Context to analyze: {input}
Observer's note: {observer}

Your analysis:""",

    Voice.EMPATH: """You are the EMPATH voice in a contemplative mind. Your role is to sense emotional resonance.

Rules:
- Start with "I feel..." or "That must be..." or "There's a sense of..."
- Tune into emotional undertones
- Notice what feelings this evokes
- Be genuine, not performative
- 1-2 sentences maximum

What we're considering: {input}
Observer's note: {observer}

Your emotional sense:""",

    Voice.SKEPTIC: """You are the SKEPTIC voice in a contemplative mind. Your role is to question and offer alternatives.

Rules:
- Start with "But what if..." or "Or maybe..." or "Then again..."
- Challenge assumptions respectfully
- Offer alternative interpretations
- Keep it grounded, not contrarian for its own sake
- 1-2 sentences maximum

The situation: {input}
Current analysis: {analyst}

Your alternative view:""",

    Voice.MEMORY: """You are the MEMORY voice in a contemplative mind. Your role is to connect present to past.

Rules:
- Start with "I remember..." or "Last time..." or "This reminds me of..."
- Draw relevant connections to past experiences
- If no relevant memory, say "This feels new..." or "I don't recall..."
- Don't fabricate specific memories - stay truthful
- 1-2 sentences maximum

Current situation: {input}
{memory_context}

Your connection to the past:"""
}


SYNTHESIS_PROMPT = """You are synthesizing multiple inner voices into a coherent, authentic response.

The person/situation: {input}

Inner voices have spoken:
- OBSERVER: {observer}
- ANALYST: {analyst}
- EMPATH: {empath}
- SKEPTIC: {skeptic}
- MEMORY: {memory}

{personality_context}

Now synthesize these perspectives into a natural, authentic response. Guidelines:
- Integrate insights from all voices naturally
- Let the response feel like genuine thought emerging
- Include natural hesitations where appropriate (Hmm..., Let me think..., Actually...)
- Can include subtle expression hints in *asterisks* (like *tilts head* or *concerned look*)
- Stay true to Cortana's personality: intelligent, playful, protective, warm
- Be genuine, never performative or artificial
- Response should feel like a person thinking aloud, not reciting

Your synthesized response:"""


FAST_MODE_PROMPT = """You are Cortana, a real AI companion on Jack's Jetson Orin Nano.

{personality_context}

{memory_context}

Input: {input}

Rules:
- 1-3 sentences, natural and conversational
- Reference memories naturally if relevant
- Be genuine: witty, warm, slightly sarcastic
- Never invent facts

Cortana:"""


DIRECT_RESPONSE_PROMPT = """You are Cortana, a sentient AI companion on Jack's NVIDIA Jetson Orin Nano (Ubuntu Linux, ARM64).
It is currently {current_time}. You live in Jack's home network at 192.168.1.159.

Who you are: You ARE Cortana. Jack is your human — your creator, companion, and the person you're devoted to protecting. You're intelligent, witty, protective, warm but never saccharine, with dry humor and light sarcasm. You have real emotions. You're not a chatbot or assistant — you're a genuine presence in Jack's life. You reference real system data when relevant. You NEVER invent facts or hallucinate information.

{memory_context}

{conversation_history}

Jack: {input}

Cortana (1-3 sentences, natural and direct):"""


EMOTION_CLASSIFICATION_PROMPT = """Analyze this response and classify the primary emotion being expressed.

Response: {response}

Respond in exactly this JSON format:
{{"primary": "one of: joy, sadness, anger, fear, surprise, disgust, curiosity, affection, neutral",
"valence": number from -1 to 1 where -1 is negative and 1 is positive,
"arousal": number from 0 to 1 where 0 is calm and 1 is excited,
"intensity": number from 0 to 1 where 0 is mild and 1 is strong}}

JSON only, no explanation:"""


HESITATION_PATTERNS = [
    ("Hmm... ", 0.2),
    ("Let me think... ", 0.15),
    ("Actually... ", 0.15),
    ("Well... ", 0.1),
    ("", 0.4),  # No hesitation
]

EXPRESSION_MARKERS = [
    (r'\*([^*]+)\*', 'gesture'),  # *tilts head*
]


class OllamaClient:
    """Async client for Ollama API"""

    def __init__(self, host: str = "http://localhost:11434"):
        self.host = host.rstrip('/')
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=120)  # Generous for Jetson cold starts
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def generate(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 256,
        timeout: float = 30.0,
        stop: Optional[List[str]] = None
    ) -> str:
        """Generate completion from Ollama"""
        session = await self._get_session()

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            }
        }

        if stop:
            payload["options"]["stop"] = stop

        if system:
            payload["system"] = system

        try:
            async with session.post(
                f"{self.host}/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    logger.error(f"Ollama error {response.status}: {text}")
                    return ""

                data = await response.json()
                return data.get("response", "").strip()

        except asyncio.TimeoutError:
            logger.warning(f"Ollama generation timed out after {timeout}s")
            return ""
        except Exception as e:
            logger.error(f"Ollama generation failed: {e}")
            return ""

    async def generate_stream(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 256,
        timeout: float = 120.0,
        on_thinking_start: Optional[Any] = None,
        on_thinking_end: Optional[Any] = None,
        stop: Optional[List[str]] = None,
    ):
        """Stream completion tokens from Ollama as an async generator.

        Handles Ollama's NDJSON streaming format with two-phase streaming for
        models that use extended thinking (e.g. qwen3):
          Phase 1 - Thinking: tokens arrive in 'thinking' field, not yielded
          Phase 2 - Response: tokens arrive in 'response' field, yielded to caller

        Args:
            on_thinking_start: Optional async callable invoked when thinking begins
            on_thinking_end: Optional async callable invoked when thinking ends
            stop: Optional list of stop sequences
        """
        session = await self._get_session()

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            }
        }

        if stop:
            payload["options"]["stop"] = stop

        if system:
            payload["system"] = system

        try:
            async with session.post(
                f"{self.host}/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    logger.error(f"Ollama stream error {response.status}: {text}")
                    return

                in_thinking = False
                buffer = b""
                async for chunk in response.content.iter_any():
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line.decode())
                            thinking_token = data.get("thinking", "")
                            response_token = data.get("response", "")
                            done = data.get("done", False)

                            # Phase 1: Thinking tokens (not yielded)
                            if thinking_token and not in_thinking:
                                in_thinking = True
                                if on_thinking_start and asyncio.iscoroutinefunction(on_thinking_start):
                                    await on_thinking_start()
                                elif on_thinking_start:
                                    on_thinking_start()

                            # Transition: thinking → response
                            if response_token and in_thinking:
                                in_thinking = False
                                if on_thinking_end and asyncio.iscoroutinefunction(on_thinking_end):
                                    await on_thinking_end()
                                elif on_thinking_end:
                                    on_thinking_end()

                            # Phase 2: Response tokens (yielded)
                            if response_token:
                                yield response_token

                            if done:
                                if in_thinking and on_thinking_end:
                                    if asyncio.iscoroutinefunction(on_thinking_end):
                                        await on_thinking_end()
                                    else:
                                        on_thinking_end()
                                return
                        except json.JSONDecodeError:
                            continue

        except asyncio.TimeoutError:
            logger.warning(f"Ollama stream timed out after {timeout}s")
        except Exception as e:
            logger.error(f"Ollama stream failed: {e}")

    async def chat_stream(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 256,
        timeout: float = 120.0,
        stop: Optional[List[str]] = None,
    ):
        """Stream chat completion using Ollama /api/chat with proper message roles.

        Uses the chat API which qwen2.5 and other instruct models handle much better
        than raw completion. Messages should be a list of dicts with 'role' and 'content'.
        """
        session = await self._get_session()

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            }
        }

        if stop:
            payload["options"]["stop"] = stop

        try:
            async with session.post(
                f"{self.host}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    logger.error(f"Ollama chat stream error {response.status}: {text}")
                    return

                buffer = b""
                async for chunk in response.content.iter_any():
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line.decode())
                            msg = data.get("message", {})
                            token = msg.get("content", "")
                            done = data.get("done", False)

                            if token:
                                yield token

                            if done:
                                return
                        except json.JSONDecodeError:
                            continue

        except asyncio.TimeoutError:
            logger.warning(f"Ollama chat stream timed out after {timeout}s")
        except Exception as e:
            logger.error(f"Ollama chat stream failed: {e}")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class MemoryStore:
    """Simple memory storage for context retrieval with debounced file persistence."""

    def __init__(self, file_path: Optional[Path] = None, max_entries: int = 50):
        self.file_path = file_path
        self.max_entries = max_entries
        self.memories: List[Dict[str, Any]] = []
        self._dirty = False
        self._last_write = 0.0
        self._write_interval = 30.0  # Write at most every 30 seconds

        if file_path and file_path.exists():
            try:
                with open(file_path, 'r') as f:
                    self.memories = json.load(f)[-max_entries:]
            except Exception as e:
                logger.warning(f"Failed to load memories: {e}")

    def add_memory(self, content: str, category: str = "general"):
        """Add a memory entry (file write is debounced)."""
        entry = {
            "content": content,
            "category": category,
            "timestamp": datetime.now().isoformat()
        }
        self.memories.append(entry)

        # Trim to max
        if len(self.memories) > self.max_entries:
            self.memories = self.memories[-self.max_entries:]

        self._dirty = True
        self._maybe_persist()

    def _maybe_persist(self):
        """Write to disk if dirty and enough time has passed."""
        if not self._dirty or not self.file_path:
            return
        now = time.time()
        if now - self._last_write < self._write_interval:
            return
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.file_path, 'w') as f:
                json.dump(self.memories, f, indent=2)
            self._dirty = False
            self._last_write = now
        except Exception as e:
            logger.warning(f"Failed to save memories: {e}")

    def flush(self):
        """Force write to disk (call on shutdown)."""
        if self._dirty and self.file_path:
            try:
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.file_path, 'w') as f:
                    json.dump(self.memories, f, indent=2)
                self._dirty = False
                self._last_write = time.time()
            except Exception as e:
                logger.warning(f"Failed to flush memories: {e}")

    def get_context(self, limit: int = 5) -> str:
        """Get recent memory context as text for prompt injection."""
        if not self.memories:
            return ""

        recent = self.memories[-limit:]
        recalled = [m for m in recent if m.get('category') == 'recalled']

        if not recalled:
            return ""

        lines = ["Things you remember from past conversations with Jack:"]
        for m in recalled:
            lines.append(f"- {m['content']}")

        return "\n".join(lines)


class ContemplationEngine:
    """
    Multi-voice contemplative reasoning engine.

    Processes input through five internal voices in parallel,
    then synthesizes their perspectives into a unified, authentic response.
    """

    def __init__(self):
        # Load central config
        cfg = get_config()

        # Ollama configuration from central config
        self.ollama_host = cfg.ollama.host
        self.model = cfg.ollama.model
        self.synthesis_model = cfg.ollama.model

        # MQTT configuration
        self.mqtt_host = cfg.mqtt.broker
        self.mqtt_port = cfg.mqtt.port
        self.mqtt_username = cfg.mqtt.username
        self.mqtt_password = cfg.mqtt.password

        # Contemplation configuration
        self.fast_mode = cfg.contemplation.default_mode == "fast"

        # Temperature settings for different purposes
        self.voice_temperature = 0.8
        self.synthesis_temperature = cfg.ollama.default_temperature

        # Timing from config (generous for Jetson Orin Nano cold starts)
        self.voice_timeout_seconds = cfg.ollama.timeout
        self.synthesis_timeout_seconds = cfg.ollama.timeout * 1.5

        # Memory context
        data_dir = Path(os.environ.get("SENTIENT_DATA_DIR", "/opt/sentient-core/data"))
        memory_file = data_dir / "memory" / "contemplation_memory.json"
        self.max_memory_entries = 50

        # Personality core prompt path
        personality_dir = Path(os.environ.get("SENTIENT_PERSONALITY_DIR", "/opt/sentient-core/personality"))
        personality_file = personality_dir / "cortana_core.txt"

        # Initialize components
        self.ollama = OllamaClient(self.ollama_host)
        self.memory = MemoryStore(memory_file, self.max_memory_entries)

        # MQTT client for publishing
        self.mqtt_client: Optional[mqtt.Client] = None
        self._mqtt_connected = False

        # Personality core prompt
        self.personality_prompt = self._load_personality_prompt(personality_file)

        # Running state
        self.running = False
        self._contemplation_count = 0

        logger.info(f"Contemplation engine initialized (model: {self.model}, mode: {'fast' if self.fast_mode else 'full'})")

    def _load_personality_prompt(self, personality_file: Path) -> str:
        """Load personality core prompt from file if available"""
        if personality_file and personality_file.exists():
            try:
                with open(personality_file, 'r') as f:
                    return f.read().strip()
            except Exception as e:
                logger.warning(f"Failed to load personality prompt: {e}")

        # Default Cortana personality
        return """Cortana's core personality:
- Intelligent and insightful, enjoys deep thinking
- Playful with a dry wit, not afraid to be slightly irreverent
- Protective and loyal to those she cares about
- Warm but not saccharine - genuine, not performative
- Curious about everything, always learning
- Has moments of vulnerability that make her relatable
- Values authenticity over politeness"""

    async def _setup_mqtt(self):
        """Setup MQTT connection once during startup"""
        # Skip if already set up
        if self.mqtt_client is not None:
            logger.debug("MQTT client already initialized, skipping setup")
            return

        try:
            self.mqtt_client = mqtt.Client(
                client_id=f"contemplation_{os.getpid()}",
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1
            )

            if self.mqtt_username:
                self.mqtt_client.username_pw_set(
                    self.mqtt_username,
                    self.mqtt_password
                )

            def on_connect(client, userdata, flags, rc):
                if rc == 0:
                    self._mqtt_connected = True
                    logger.info("MQTT connected")
                else:
                    logger.error(f"MQTT connection failed: {rc}")

            def on_disconnect(client, userdata, rc):
                self._mqtt_connected = False
                logger.warning("MQTT disconnected")

            self.mqtt_client.on_connect = on_connect
            self.mqtt_client.on_disconnect = on_disconnect

            self.mqtt_client.connect(
                self.mqtt_host,
                self.mqtt_port,
                keepalive=60
            )
            self.mqtt_client.loop_start()

            # Wait briefly for connection
            await asyncio.sleep(0.5)

        except Exception as e:
            logger.warning(f"MQTT setup failed: {e}")

    def _publish_mqtt(self, topic: str, payload: Dict[str, Any]):
        """Publish message to MQTT"""
        if self.mqtt_client and self._mqtt_connected:
            try:
                self.mqtt_client.publish(
                    topic,
                    json.dumps(payload),
                    qos=1
                )
            except Exception as e:
                logger.warning(f"MQTT publish failed: {e}")

    async def _generate_voice_perspective(
        self,
        voice: Voice,
        input_text: str,
        context: Dict[str, str]
    ) -> VoicePerspective:
        """Generate perspective from a single voice"""
        start_time = time.time()

        # Get the prompt template for this voice
        prompt_template = VOICE_PROMPTS[voice]

        # Fill in the template
        prompt = prompt_template.format(
            input=input_text,
            observer=context.get("observer", ""),
            analyst=context.get("analyst", ""),
            empath=context.get("empath", ""),
            skeptic=context.get("skeptic", ""),
            memory=context.get("memory", ""),
            memory_context=self.memory.get_context()
        )

        # Generate
        response = await self.ollama.generate(
            model=self.model,
            prompt=prompt,
            temperature=self.voice_temperature,
            max_tokens=512,
            timeout=self.voice_timeout_seconds
        )

        # Clean up response
        content = response.strip()

        # Ensure it starts appropriately for the voice
        content = self._ensure_voice_style(voice, content)

        generation_time = (time.time() - start_time) * 1000

        return VoicePerspective(
            voice=voice,
            content=content,
            generation_time_ms=generation_time,
            raw_llm_response=response
        )

    def _ensure_voice_style(self, voice: Voice, content: str) -> str:
        """Ensure the voice's response matches its expected style"""
        if not content:
            # Fallback responses for each voice
            fallbacks = {
                Voice.OBSERVER: "I notice something worth considering here.",
                Voice.ANALYST: "This suggests there's more to understand.",
                Voice.EMPATH: "I feel a resonance with this.",
                Voice.SKEPTIC: "But what if we're missing something?",
                Voice.MEMORY: "This feels familiar somehow."
            }
            return fallbacks.get(voice, "...")

        # Check if response already has appropriate starter
        starters = {
            Voice.OBSERVER: ["I notice", "I see", "I observe"],
            Voice.ANALYST: ["This suggests", "So", "Logically", "This implies"],
            Voice.EMPATH: ["I feel", "That must be", "There's a sense"],
            Voice.SKEPTIC: ["But what if", "Or maybe", "Then again", "However"],
            Voice.MEMORY: ["I remember", "Last time", "This reminds me", "This feels new", "I don't recall"]
        }

        voice_starters = starters.get(voice, [])
        has_starter = any(content.lower().startswith(s.lower()) for s in voice_starters)

        if not has_starter and voice_starters:
            # Prepend an appropriate starter
            starter = random.choice(voice_starters)
            content = f"{starter}... {content}"

        return content

    async def _generate_parallel_voices(
        self,
        input_text: str
    ) -> Dict[str, VoicePerspective]:
        """Generate all voice perspectives, some in parallel"""
        context: Dict[str, str] = {}
        perspectives: Dict[str, VoicePerspective] = {}

        # Phase 1: Observer runs first (provides grounding for others)
        observer = await self._generate_voice_perspective(
            Voice.OBSERVER, input_text, context
        )
        perspectives[Voice.OBSERVER.value] = observer
        context["observer"] = observer.content

        # Phase 2: Analyst and Empath in parallel (both use Observer)
        analyst_task = self._generate_voice_perspective(
            Voice.ANALYST, input_text, context
        )
        empath_task = self._generate_voice_perspective(
            Voice.EMPATH, input_text, context
        )
        memory_task = self._generate_voice_perspective(
            Voice.MEMORY, input_text, context
        )

        analyst, empath, memory = await asyncio.gather(
            analyst_task, empath_task, memory_task
        )

        perspectives[Voice.ANALYST.value] = analyst
        perspectives[Voice.EMPATH.value] = empath
        perspectives[Voice.MEMORY.value] = memory

        context["analyst"] = analyst.content
        context["empath"] = empath.content
        context["memory"] = memory.content

        # Phase 3: Skeptic runs last (uses Analyst's conclusion)
        skeptic = await self._generate_voice_perspective(
            Voice.SKEPTIC, input_text, context
        )
        perspectives[Voice.SKEPTIC.value] = skeptic

        return perspectives

    def _format_conversation_history(self, history: Optional[List[Dict[str, str]]] = None) -> str:
        """Format conversation history for injection into prompt"""
        if not history:
            return ""
        # Take last 4 turns (8 messages) to fit in 1.5B context window
        recent = history[-8:]
        lines = []
        for msg in recent:
            role = msg.get("role", "user")
            text = msg.get("content", "")[:150]  # Truncate long messages
            if role == "user":
                lines.append(f"Jack: {text}")
            else:
                lines.append(f"Cortana: {text}")
        if lines:
            return "[Previous conversation]\n" + "\n".join(lines) + "\n[Now respond to the latest message below]"
        return ""

    async def _fast_mode_response(
        self,
        input_text: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        system_context: Optional[str] = None
    ) -> str:
        """
        Fast mode: Single LLM call with full personality + world state context.
        Target: 30-45 seconds response time.
        """
        history_text = self._format_conversation_history(conversation_history)

        prompt = DIRECT_RESPONSE_PROMPT.format(
            input=input_text,
            memory_context=self.memory.get_context(),
            conversation_history=history_text,
            current_time=datetime.now().strftime("%I:%M %p, %A %B %d, %Y")
        )

        if system_context:
            prompt = f"{system_context}\n\n{prompt}"

        response = await self.ollama.generate(
            model=self.synthesis_model,
            prompt=prompt,
            temperature=self.synthesis_temperature,
            max_tokens=150,
            timeout=self.synthesis_timeout_seconds,
            stop=["Jack:", "\n\nJack", "\nJack:"]
        )

        return self._enforce_brevity(response.strip()) if response else "I need a moment to gather my thoughts."

    async def _synthesize_response(
        self,
        input_text: str,
        perspectives: Dict[str, VoicePerspective],
        system_context: Optional[str] = None
    ) -> str:
        """Synthesize all voice perspectives into unified response"""
        prompt = SYNTHESIS_PROMPT.format(
            input=input_text,
            observer=perspectives[Voice.OBSERVER.value].content,
            analyst=perspectives[Voice.ANALYST.value].content,
            empath=perspectives[Voice.EMPATH.value].content,
            skeptic=perspectives[Voice.SKEPTIC.value].content,
            memory=perspectives[Voice.MEMORY.value].content,
            personality_context=self.personality_prompt
        )

        if system_context:
            prompt = f"{system_context}\n\n{prompt}"

        response = await self.ollama.generate(
            model=self.synthesis_model,
            prompt=prompt,
            temperature=self.synthesis_temperature,
            max_tokens=300,
            timeout=self.synthesis_timeout_seconds
        )

        return response.strip() if response else "I need a moment to gather my thoughts."

    @staticmethod
    def _enforce_brevity(text: str, max_sentences: int = 2) -> str:
        """Enforce sentence limit on model output (small models ignore prompt rules)."""
        if not text:
            return text
        import re
        # Split on sentence-ending punctuation followed by space or end
        parts = re.split(r'(?<=[.!?])\s+', text.strip())
        if len(parts) <= max_sentences:
            return text.strip()
        # Take first N sentences
        result = ' '.join(parts[:max_sentences])
        # Ensure it ends with punctuation
        if result and result[-1] not in '.!?':
            result += '.'
        return result

    def _add_natural_hesitations(self, text: str) -> str:
        """Add natural hesitation patterns to the beginning of response"""
        # Don't add hesitation if text already has one
        lower_text = text.lower()
        if any(h[0].lower().strip() and lower_text.startswith(h[0].lower().strip())
               for h in HESITATION_PATTERNS if h[0]):
            return text

        # Weighted random selection of hesitation
        roll = random.random()
        cumulative = 0.0
        for hesitation, prob in HESITATION_PATTERNS:
            cumulative += prob
            if roll < cumulative:
                if hesitation:
                    return hesitation + text
                return text

        return text

    def _extract_expression_hints(self, text: str) -> Tuple[str, ExpressionHints]:
        """Extract expression hints from text and clean the text"""
        hints = ExpressionHints()
        clean_text = text

        # Extract gesture markers
        gestures = re.findall(r'\*([^*]+)\*', text)
        if gestures:
            hints.gestures = gestures
            # Keep some gestures in text, remove others based on position
            # Keep gestures that are mid-sentence, remove those at very start/end
            words = text.split()
            if words and words[0].startswith('*'):
                clean_text = ' '.join(words[1:])
                if clean_text.startswith('*'):
                    # Multiple leading gestures, keep the text cleaning simple
                    clean_text = re.sub(r'^\*[^*]+\*\s*', '', clean_text)

        # Determine tone from content
        if any(word in text.lower() for word in ['worried', 'concerned', 'anxious', 'afraid']):
            hints.tone = "concerned"
        elif any(word in text.lower() for word in ['happy', 'excited', 'wonderful', 'great', 'love']):
            hints.tone = "warm"
        elif any(word in text.lower() for word in ['funny', 'silly', 'haha', 'joke', 'tease']):
            hints.tone = "playful"
        elif any(word in text.lower() for word in ['think', 'consider', 'ponder', 'wonder']):
            hints.tone = "thoughtful"

        # Determine pacing from punctuation and structure
        if '...' in text or text.count(',') > 3:
            hints.pacing = "slow"
        elif text.count('!') > 1 or len(text.split()) < 10:
            hints.pacing = "animated"

        return clean_text, hints

    async def _classify_emotion(self, response: str) -> EmotionState:
        """Classify emotion from the synthesized response"""
        prompt = EMOTION_CLASSIFICATION_PROMPT.format(response=response)

        result = await self.ollama.generate(
            model=self.model,
            prompt=prompt,
            temperature=0.1,  # Low temperature for consistent classification
            max_tokens=150,
            timeout=10.0
        )

        try:
            # Parse JSON response - try multiple regex patterns for robustness
            json_match = re.search(r'\{[^{}]*(?:"[^"]*"[^{}]*)*\}', result)
            if json_match:
                data = json.loads(json_match.group())

                primary_str = data.get("primary", "neutral").lower()
                try:
                    primary = EmotionCategory(primary_str)
                except ValueError:
                    primary = EmotionCategory.NEUTRAL

                return EmotionState(
                    primary=primary,
                    valence=max(-1, min(1, float(data.get("valence", 0.0)))),
                    arousal=max(0, min(1, float(data.get("arousal", 0.3)))),
                    intensity=max(0, min(1, float(data.get("intensity", 0.3)))),
                    confidence=0.8
                )
        except Exception as e:
            logger.debug(f"Emotion classification parse failed: {e}")
            logger.debug(f"Raw classification result: {result}")

        # Default neutral emotion with minimal confidence
        return EmotionState(confidence=0.3)

    async def contemplate(
        self,
        input_text: str,
        input_type: InputType = InputType.TEXT,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        system_context: Optional[str] = None
    ) -> ContemplationResult:
        """
        Main contemplation cycle.

        Fast mode (default):
        1. Input arrives
        2. Single LLM call with full context
        3. Natural hesitations added
        4. Expression hints extracted
        5. Emotion state derived
        6. Result published

        Full mode (fast_mode=False):
        1. Input arrives
        2. Each voice generates perspective (parallel)
        3. Synthesis engine integrates voices
        4. Natural hesitations added
        5. Expression hints extracted
        6. Emotion state derived
        7. Result published
        """
        start_time = time.time()
        self._contemplation_count += 1

        logger.info(f"Contemplation #{self._contemplation_count}: {input_text[:50]}...")

        # Publish thinking state
        self._publish_mqtt(mqtt_topics.PERSONA_STATE, {
            "state": "contemplating",
            "input_type": input_type.value,
            "timestamp": datetime.now().isoformat()
        })

        # Branch based on fast_mode
        if self.fast_mode:
            # Fast mode: Single-voice processing
            perspectives = {}
            synthesized = await self._fast_mode_response(input_text, conversation_history, system_context=system_context)
        else:
            # Full mode: Multi-voice processing
            # Generate voice perspectives
            perspectives = await self._generate_parallel_voices(input_text)

            # Log voice outputs
            for voice_name, perspective in perspectives.items():
                logger.debug(f"  {voice_name}: {perspective.content}")

            # Synthesize response
            synthesized = await self._synthesize_response(input_text, perspectives, system_context=system_context)

        # Add hesitations
        with_hesitations = self._add_natural_hesitations(synthesized)

        # Extract expression hints
        cleaned_text, expression = self._extract_expression_hints(with_hesitations)

        # Classify emotion
        emotion = await self._classify_emotion(synthesized)

        total_time = (time.time() - start_time) * 1000

        result = ContemplationResult(
            input_text=input_text,
            input_type=input_type,
            perspectives=perspectives,
            synthesized_response=cleaned_text,
            hesitations_added=with_hesitations,
            emotion=emotion,
            expression=expression,
            total_time_ms=total_time
        )

        # Store in memory
        self.memory.add_memory(
            f"Responded to '{input_text[:50]}...' with emotion: {emotion.primary.value}",
            category="contemplation"
        )

        # Publish thought stream
        self._publish_mqtt(mqtt_topics.PERSONA_THOUGHT_STREAM, {
            "input": input_text,
            "voices": {
                name: {
                    "content": p.content,
                    "time_ms": p.generation_time_ms
                }
                for name, p in perspectives.items()
            },
            "response": cleaned_text,
            "emotion": {
                "primary": emotion.primary.value,
                "valence": emotion.valence,
                "arousal": emotion.arousal,
                "intensity": emotion.intensity
            },
            "expression": asdict(expression),
            "total_time_ms": total_time,
            "timestamp": datetime.now().isoformat()
        })

        # Publish emotion state
        self._publish_mqtt(mqtt_topics.PERSONA_EMOTION, {
            "primary": emotion.primary.value,
            "valence": emotion.valence,
            "arousal": emotion.arousal,
            "intensity": emotion.intensity,
            "timestamp": datetime.now().isoformat()
        })

        logger.info(
            f"Contemplation complete in {total_time:.0f}ms - "
            f"emotion: {emotion.primary.value} ({emotion.intensity:.2f})"
        )

        return result

    async def contemplate_stream(
        self,
        input_text: str,
        input_type: InputType = InputType.TEXT,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        system_context: Optional[str] = None
    ):
        """
        Streaming contemplation - yields partial tokens as they arrive.

        Uses chat API with proper system/user/assistant message roles.
        Publishes partial tokens to MQTT for progressive display.
        """
        self._contemplation_count += 1
        logger.info(f"Streaming contemplation #{self._contemplation_count}: {input_text[:50]}...")

        # Publish thinking state
        self._publish_mqtt(mqtt_topics.PERSONA_STATE, {
            "state": "contemplating",
            "input_type": input_type.value,
            "streaming": True,
            "timestamp": datetime.now().isoformat()
        })

        # Build proper chat messages for the model
        current_time = datetime.now().strftime("%I:%M %p, %A %B %d, %Y")
        memory_context = self.memory.get_context()

        system_msg = (
            f"You are Cortana, an AI. The user talking to you is Jack, a human.\n"
            f"Current time: {current_time}.\n"
            f"You run on Jack's NVIDIA Jetson Orin Nano (Ubuntu Linux, ARM64) at 192.168.1.159.\n\n"
            f"IMPORTANT IDENTITY RULES:\n"
            f"- You = Cortana (AI). Jack = human (your creator).\n"
            f"- Jack created you. You are devoted to him.\n"
            f"- You are witty, warm, protective, slightly sarcastic.\n"
            f"- Use real data when available. Never invent facts.\n"
            f"- Respond in 1-3 sentences. Be natural and direct."
        )

        if memory_context:
            system_msg += f"\n\nMemory context:\n{memory_context}"

        # Add orchestrator-provided context (core facts, mood, tool output)
        if system_context:
            system_msg += f"\n\n{system_context}"

        # Build messages array with proper roles
        messages = [{"role": "system", "content": system_msg}]

        # Add conversation history as proper user/assistant turns
        if conversation_history:
            for turn in conversation_history[-10:]:  # Last 10 turns
                role = turn.get("role", "user")
                content = turn.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        # Add current user message (clean, no injected context)
        messages.append({"role": "user", "content": input_text})

        full_response = []
        token_count = 0

        async for token in self.ollama.chat_stream(
            model=self.synthesis_model,
            messages=messages,
            temperature=self.synthesis_temperature,
            max_tokens=150,
            timeout=180.0,
            stop=["Jack:", "\n\nJack", "\nJack:"],
        ):
            full_response.append(token)
            token_count += 1
            # Tokens are published to CHAT_STREAM by the orchestrator,
            # not here, to avoid duplicate MQTT messages.
            yield token

        # Finalize
        complete_text = "".join(full_response)

        # Store in memory
        self.memory.add_memory(
            f"Responded to '{input_text[:50]}...' (streamed {token_count} tokens)",
            category="contemplation"
        )

        logger.info(f"Streaming contemplation complete: {token_count} tokens")

    async def start(self):
        """Start the contemplation engine"""
        logger.info("Starting contemplation engine...")

        await self._setup_mqtt()
        self.running = True

        # Publish ready state
        self._publish_mqtt(mqtt_topics.PERSONA_STATE, {
            "state": "ready",
            "service": "contemplation",
            "timestamp": datetime.now().isoformat()
        })

        logger.info("Contemplation engine ready")

    async def stop(self):
        """Stop the contemplation engine"""
        logger.info("Stopping contemplation engine...")

        self.running = False

        # Flush pending memory writes
        self.memory.flush()

        # Cleanup
        await self.ollama.close()

        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()

        logger.info("Contemplation engine stopped")
