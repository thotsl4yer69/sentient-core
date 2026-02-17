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
import logging
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

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("contemplation")


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


@dataclass
class ContemplationConfig:
    """Configuration for the contemplation engine"""
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:1b"
    synthesis_model: str = "llama3.2:1b"

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: Optional[str] = "sentient"
    mqtt_password: Optional[str] = "sentient1312"

    # Temperature settings for different purposes
    voice_temperature: float = 0.8
    synthesis_temperature: float = 0.7

    # Timing (generous for Jetson Orin Nano cold starts)
    voice_timeout_seconds: float = 45.0
    synthesis_timeout_seconds: float = 60.0

    # Memory context
    memory_context_file: Optional[Path] = None
    max_memory_entries: int = 50

    # Personality core prompt path
    personality_prompt_file: Optional[Path] = None

    # Fast mode: Single-voice processing for production performance
    fast_mode: bool = True


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


FAST_MODE_PROMPT = """You are Cortana, responding to input with authentic contemplation.

{personality_context}

{memory_context}

Input: {input}

Guidelines:
- Think naturally, integrating observation, analysis, emotional awareness, and skepticism
- Include natural hesitations where appropriate (Hmm..., Let me think..., Actually...)
- Can include subtle expression hints in *asterisks* (like *tilts head* or *concerned look*)
- Stay true to Cortana's personality: intelligent, playful, protective, warm
- Be genuine, never performative or artificial
- Response should feel like a person thinking aloud, not reciting
- Express your emotional state naturally through word choice, tone, and pacing

Your contemplated response:"""


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
        timeout: float = 30.0
    ) -> str:
        """Generate completion from Ollama"""
        session = await self._get_session()

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            }
        }

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

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class MemoryStore:
    """Simple memory storage for context retrieval"""

    def __init__(self, file_path: Optional[Path] = None, max_entries: int = 50):
        self.file_path = file_path
        self.max_entries = max_entries
        self.memories: List[Dict[str, Any]] = []

        if file_path and file_path.exists():
            try:
                with open(file_path, 'r') as f:
                    self.memories = json.load(f)[-max_entries:]
            except Exception as e:
                logger.warning(f"Failed to load memories: {e}")

    def add_memory(self, content: str, category: str = "general"):
        """Add a memory entry"""
        entry = {
            "content": content,
            "category": category,
            "timestamp": datetime.now().isoformat()
        }
        self.memories.append(entry)

        # Trim to max
        if len(self.memories) > self.max_entries:
            self.memories = self.memories[-self.max_entries:]

        # Persist
        if self.file_path:
            try:
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.file_path, 'w') as f:
                    json.dump(self.memories, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to save memories: {e}")

    def get_context(self, limit: int = 5) -> str:
        """Get recent memory context as text"""
        if not self.memories:
            return "No prior memories to draw from."

        recent = self.memories[-limit:]
        lines = [f"- {m['content']}" for m in recent]
        return "Recent memories:\n" + "\n".join(lines)


class ContemplationEngine:
    """
    Multi-voice contemplative reasoning engine.

    Processes input through five internal voices in parallel,
    then synthesizes their perspectives into a unified, authentic response.
    """

    def __init__(self, config: Optional[ContemplationConfig] = None):
        self.config = config or ContemplationConfig()
        self.ollama = OllamaClient(self.config.ollama_host)
        self.memory = MemoryStore(
            self.config.memory_context_file,
            self.config.max_memory_entries
        )

        # MQTT client for publishing
        self.mqtt_client: Optional[mqtt.Client] = None
        self._mqtt_connected = False

        # Personality core prompt
        self.personality_prompt = self._load_personality_prompt()

        # Running state
        self.running = False
        self._contemplation_count = 0

        logger.info("Contemplation engine initialized")

    def _load_personality_prompt(self) -> str:
        """Load personality core prompt from file if available"""
        if self.config.personality_prompt_file and self.config.personality_prompt_file.exists():
            try:
                with open(self.config.personality_prompt_file, 'r') as f:
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

            if self.config.mqtt_username:
                self.mqtt_client.username_pw_set(
                    self.config.mqtt_username,
                    self.config.mqtt_password
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
                self.config.mqtt_host,
                self.config.mqtt_port,
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
            model=self.config.ollama_model,
            prompt=prompt,
            temperature=self.config.voice_temperature,
            max_tokens=100,
            timeout=self.config.voice_timeout_seconds
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

    async def _fast_mode_response(
        self,
        input_text: str
    ) -> str:
        """
        Fast mode: Single LLM call with full personality + world state context.
        Target: 30-45 seconds response time.
        """
        prompt = FAST_MODE_PROMPT.format(
            input=input_text,
            personality_context=self.personality_prompt,
            memory_context=self.memory.get_context()
        )

        response = await self.ollama.generate(
            model=self.config.synthesis_model,
            prompt=prompt,
            temperature=self.config.synthesis_temperature,
            max_tokens=400,
            timeout=self.config.synthesis_timeout_seconds
        )

        return response.strip() if response else "I need a moment to gather my thoughts."

    async def _synthesize_response(
        self,
        input_text: str,
        perspectives: Dict[str, VoicePerspective]
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

        response = await self.ollama.generate(
            model=self.config.synthesis_model,
            prompt=prompt,
            temperature=self.config.synthesis_temperature,
            max_tokens=400,
            timeout=self.config.synthesis_timeout_seconds
        )

        return response.strip() if response else "I need a moment to gather my thoughts."

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
            model=self.config.ollama_model,
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
        input_type: InputType = InputType.TEXT
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
        self._publish_mqtt("sentient/persona/state", {
            "state": "contemplating",
            "input_type": input_type.value,
            "timestamp": datetime.now().isoformat()
        })

        # Branch based on fast_mode
        if self.config.fast_mode:
            # Fast mode: Single-voice processing
            perspectives = {}
            synthesized = await self._fast_mode_response(input_text)
        else:
            # Full mode: Multi-voice processing
            # Generate voice perspectives
            perspectives = await self._generate_parallel_voices(input_text)

            # Log voice outputs
            for voice_name, perspective in perspectives.items():
                logger.debug(f"  {voice_name}: {perspective.content}")

            # Synthesize response
            synthesized = await self._synthesize_response(input_text, perspectives)

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
        self._publish_mqtt("sentient/persona/thought_stream", {
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
        self._publish_mqtt("sentient/persona/emotion", {
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

    async def start(self):
        """Start the contemplation engine"""
        logger.info("Starting contemplation engine...")

        await self._setup_mqtt()
        self.running = True

        # Publish ready state
        self._publish_mqtt("sentient/persona/state", {
            "state": "ready",
            "service": "contemplation",
            "timestamp": datetime.now().isoformat()
        })

        logger.info("Contemplation engine ready")

    async def stop(self):
        """Stop the contemplation engine"""
        logger.info("Stopping contemplation engine...")

        self.running = False

        # Cleanup
        await self.ollama.close()

        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()

        logger.info("Contemplation engine stopped")


async def interactive_demo():
    """Interactive demo of the contemplation engine"""
    print("\n" + "="*60)
    print("CORTANA CONTEMPLATIVE REASONING ENGINE")
    print("="*60)
    print("Type a message to see how Cortana contemplates.\n")
    print("Commands: 'quit' to exit\n")

    # Configure with defaults
    config = ContemplationConfig(
        ollama_host="http://localhost:11434",
        ollama_model="llama3.2:1b",
        memory_context_file=Path("/home/cortana/sentient-core/data/memory/contemplation_memory.json")
    )

    engine = ContemplationEngine(config)

    try:
        await engine.start()

        while True:
            try:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, input, "\nYou: "
                )

                if user_input.lower() in ['quit', 'exit', 'q']:
                    break

                if not user_input.strip():
                    continue

                print("\n[Contemplating...]\n")

                result = await engine.contemplate(user_input, InputType.TEXT)

                # Display voice perspectives
                print("-" * 40)
                for voice_name, perspective in result.perspectives.items():
                    print(f"  {voice_name.upper()}: {perspective.content}")
                print("-" * 40)

                # Display final response
                print(f"\nCortana: {result.synthesized_response}")

                # Display emotional state
                print(f"\n[Emotion: {result.emotion.primary.value} | "
                      f"Valence: {result.emotion.valence:.2f} | "
                      f"Arousal: {result.emotion.arousal:.2f}]")

                if result.expression.gestures:
                    print(f"[Expressions: {', '.join(result.expression.gestures)}]")

                print(f"[Time: {result.total_time_ms:.0f}ms]")

            except (KeyboardInterrupt, EOFError):
                break

    finally:
        await engine.stop()
        print("\nContemplation engine stopped.")


async def main():
    """Main entry point - runs as systemd service"""
    config = ContemplationConfig(
        ollama_host="http://localhost:11434",
        ollama_model="llama3.2:1b",
        memory_context_file=Path("/home/cortana/sentient-core/data/memory/contemplation_memory.json")
    )

    engine = ContemplationEngine(config)

    try:
        await engine.start()

        # Keep service running
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Service interrupted")
    finally:
        await engine.stop()


async def interactive_main():
    """Interactive demo - only for direct script execution"""
    await interactive_demo()


if __name__ == "__main__":
    asyncio.run(main())
