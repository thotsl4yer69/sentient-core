#!/usr/bin/env python3
"""
Proactive Behavior Engine - Cortana's Autonomous Initiative System

Cortana initiates interaction based on internal triggers, NOT random timers.

Triggers:
- BOREDOM: No interaction 30+ min while Jack present → comment/question/thought
- CONCERN: Threat detected or anomaly → alert naturally
- CURIOSITY: Interesting sensor data → "Hey, I noticed..."
- CARE: Time-based check-ins if Jack stressed or late
- EXCITEMENT: System improvement/detection → share enthusiasm

Architecture:
- Background asyncio task evaluating triggers every 30 seconds
- Probability-based activation (not every trigger fires)
- Cooldown periods to prevent annoyance
- Proactive messages go through contemplation engine API
- Can be verbal (MQTT to TTS) or notification (ntfy)
- Subscribe to world state for context
- Track last interaction time in Redis
"""

import asyncio
import json
import os
import time
import random
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from enum import Enum

import redis.asyncio as redis
import aiohttp

from sentient.config import get_config
from sentient.common.service_base import SentientService
from sentient.common.mqtt_topics import (
    TTS_SPEAK, PERCEPTION_STATE, CHAT_OUTPUT, AVATAR_EXPRESSION,
    NETWORK_DEVICE_ARRIVED, NETWORK_DEVICE_DEPARTED, NOTIFICATION_SEND
)
from sentient.common.logging import setup_logging


class TriggerType(str, Enum):
    """Types of proactive triggers"""
    BOREDOM = "boredom"
    CONCERN = "concern"
    CURIOSITY = "curiosity"
    CARE = "care"
    EXCITEMENT = "excitement"
    SYSTEM_OBSERVATION = "system_observation"
    IDLE_THOUGHT = "idle_thought"
    NETWORK_EVENT = "network_event"
    REMINDER = "reminder"
    DAILY_BRIEFING = "daily_briefing"
    MEMORY_FOLLOWUP = "memory_followup"


@dataclass
class TriggerConfig:
    """Configuration for a trigger type"""
    trigger_type: TriggerType
    evaluation_interval: int  # seconds
    cooldown_period: int  # seconds
    activation_probability: float  # 0.0 to 1.0
    min_confidence: float  # minimum confidence to activate


@dataclass
class ProactiveMessage:
    """A proactive message to be sent"""
    trigger_type: TriggerType
    content: str
    delivery_method: str  # "voice", "notification", "both"
    priority: int  # 1-10
    timestamp: str
    context: Dict[str, Any]


class ProactiveBehaviorEngine(SentientService):
    """
    Autonomous proactive behavior engine.
    Evaluates triggers and initiates interactions naturally.
    """

    def __init__(self):
        """Initialize proactive behavior engine"""
        super().__init__(name="proactive", http_port=None)

        cfg = get_config()

        # Build contemplation API URL from config
        self.contemplation_url = f"http://localhost:{cfg.contemplation.port}/generate"

        # Build ntfy URL from config
        self.ntfy_url = cfg.ntfy.server
        self.ntfy_topic = cfg.ntfy.topic

        # Redis client (separate from base class as we need specific features)
        self.redis_client: Optional[redis.Redis] = None

        # HTTP session for API calls
        self.http_session: Optional[aiohttp.ClientSession] = None

        # Current world state (updated from MQTT)
        self.world_state: Dict[str, Any] = {}
        self.last_world_state_update: float = 0.0

        # Trigger configurations
        self.trigger_configs = {
            TriggerType.BOREDOM: TriggerConfig(
                trigger_type=TriggerType.BOREDOM,
                evaluation_interval=60,  # Check every minute
                cooldown_period=600,  # 10 min cooldown (was 30)
                activation_probability=0.5,  # 50% chance when triggered
                min_confidence=0.6
            ),
            TriggerType.CONCERN: TriggerConfig(
                trigger_type=TriggerType.CONCERN,
                evaluation_interval=30,  # Check every 30s
                cooldown_period=300,  # 5 min cooldown
                activation_probability=0.8,  # 80% chance when triggered
                min_confidence=0.6
            ),
            TriggerType.CURIOSITY: TriggerConfig(
                trigger_type=TriggerType.CURIOSITY,
                evaluation_interval=120,  # Check every 2 min
                cooldown_period=600,  # 10 min cooldown (was 15)
                activation_probability=0.5,  # 50% chance when triggered
                min_confidence=0.65
            ),
            TriggerType.CARE: TriggerConfig(
                trigger_type=TriggerType.CARE,
                evaluation_interval=300,  # Check every 5 min
                cooldown_period=1800,  # 30 min cooldown (was 1 hour)
                activation_probability=0.6,  # 60% chance when triggered
                min_confidence=0.7
            ),
            TriggerType.EXCITEMENT: TriggerConfig(
                trigger_type=TriggerType.EXCITEMENT,
                evaluation_interval=60,  # Check every minute
                cooldown_period=600,  # 10 min cooldown
                activation_probability=0.7,  # 70% chance when triggered
                min_confidence=0.75
            ),
            TriggerType.SYSTEM_OBSERVATION: TriggerConfig(
                trigger_type=TriggerType.SYSTEM_OBSERVATION,
                evaluation_interval=180,  # Check every 3 min
                cooldown_period=900,  # 15 min cooldown
                activation_probability=0.4,  # 40% chance
                min_confidence=0.6
            ),
            TriggerType.IDLE_THOUGHT: TriggerConfig(
                trigger_type=TriggerType.IDLE_THOUGHT,
                evaluation_interval=120,  # Check every 2 min
                cooldown_period=900,  # 15 min cooldown between idle thoughts
                activation_probability=0.3,  # 30% chance when triggered
                min_confidence=0.5
            ),
            TriggerType.NETWORK_EVENT: TriggerConfig(
                trigger_type=TriggerType.NETWORK_EVENT,
                evaluation_interval=30,
                cooldown_period=300,  # 5 min cooldown between network notifications
                activation_probability=0.9,  # High probability - these are important
                min_confidence=0.6
            ),
            TriggerType.REMINDER: TriggerConfig(
                trigger_type=TriggerType.REMINDER,
                evaluation_interval=15,  # Check every 15s for due reminders
                cooldown_period=10,  # Very short cooldown - reminders are time-critical
                activation_probability=1.0,  # Always fire when due
                min_confidence=0.0
            ),
            TriggerType.DAILY_BRIEFING: TriggerConfig(
                trigger_type=TriggerType.DAILY_BRIEFING,
                evaluation_interval=60,  # Check every minute
                cooldown_period=21600,  # 6 hour cooldown (max 2 briefings/day)
                activation_probability=1.0,  # Always fire at scheduled times
                min_confidence=0.0
            ),
            TriggerType.MEMORY_FOLLOWUP: TriggerConfig(
                trigger_type=TriggerType.MEMORY_FOLLOWUP,
                evaluation_interval=300,  # Check every 5 min
                cooldown_period=7200,  # 2 hour cooldown
                activation_probability=0.25,  # 25% chance when triggered
                min_confidence=0.0
            ),
        }

        # Last trigger activation times (for cooldown tracking)
        self.last_activation: Dict[TriggerType, float] = {}

        # Evaluation loop control
        self.evaluation_task: Optional[asyncio.Task] = None
        self.world_state_task: Optional[asyncio.Task] = None

        # Network event queue
        self.pending_network_events: List[Dict[str, Any]] = []

        # Weather cache (in-memory fallback if Redis fails)
        self._weather_cache: Optional[Dict[str, Any]] = None
        self._weather_cache_time: float = 0.0

        self.logger.info("ProactiveBehaviorEngine initialized")

    async def setup(self):
        """Service-specific initialization"""
        # Connect to Redis
        try:
            self.redis_client = await redis.from_url(
                f"redis://{self.config.redis.host}:{self.config.redis.port}",
                decode_responses=True
            )
            await self.redis_client.ping()
            self.logger.info(f"✓ Connected to Redis at {self.config.redis.host}:{self.config.redis.port}")
        except Exception as e:
            self.logger.error(f"Failed to connect to Redis: {e}")
            raise

        # Create HTTP session
        self.http_session = aiohttp.ClientSession()
        self.logger.info("✓ HTTP session created")

        # Register MQTT handler for world state updates
        self.on_mqtt(PERCEPTION_STATE)(self._on_world_state)

        # Subscribe to network device events
        self.on_mqtt(NETWORK_DEVICE_ARRIVED)(self._on_network_device_arrived)
        self.on_mqtt(NETWORK_DEVICE_DEPARTED)(self._on_network_device_departed)

        # Start evaluation loop
        self.evaluation_task = asyncio.create_task(self.evaluation_loop())

        self.logger.info("All connections established")

    async def teardown(self):
        """Service-specific cleanup"""
        # Cancel evaluation task
        if self.evaluation_task:
            self.evaluation_task.cancel()
            try:
                await self.evaluation_task
            except asyncio.CancelledError:
                pass

        if self.redis_client:
            await self.redis_client.close()
            self.logger.info("Redis connection closed")

        if self.http_session:
            await self.http_session.close()
            self.logger.info("HTTP session closed")

    async def _on_world_state(self, topic: str, payload: bytes):
        """Handle world state updates from MQTT"""
        try:
            self.world_state = json.loads(payload.decode())
            self.last_world_state_update = time.time()
            self.logger.debug(f"World state updated: jack_present={self.world_state.get('jack_present')}")
        except Exception as e:
            self.logger.error(f"Error processing world state: {e}")

    async def _on_network_device_arrived(self, topic: str, payload: bytes):
        """Handle new device arriving on network"""
        try:
            device = json.loads(payload.decode())
            self.logger.info(f"Network device arrived: {device.get('name') or device.get('hostname') or device.get('mac')} ({device.get('ip')})")
            self.pending_network_events.append({
                'event': 'arrived',
                'device': device,
                'timestamp': time.time()
            })
        except Exception as e:
            self.logger.error(f"Error processing device arrival: {e}")

    async def _on_network_device_departed(self, topic: str, payload: bytes):
        """Handle device leaving network"""
        try:
            device = json.loads(payload.decode())
            self.logger.info(f"Network device departed: {device.get('name') or device.get('hostname') or device.get('mac')} ({device.get('ip')})")
            self.pending_network_events.append({
                'event': 'departed',
                'device': device,
                'timestamp': time.time()
            })
        except Exception as e:
            self.logger.error(f"Error processing device departure: {e}")

    async def get_last_interaction_time(self) -> float:
        """Get timestamp of last interaction from Redis"""
        try:
            last_interaction_str = await self.redis_client.get("interaction:last_timestamp")
            if last_interaction_str:
                return float(last_interaction_str)
            return 0.0
        except Exception as e:
            self.logger.error(f"Error getting last interaction time: {e}")
            return 0.0

    async def set_last_interaction_time(self, timestamp: float):
        """Set timestamp of last interaction in Redis"""
        try:
            await self.redis_client.set("interaction:last_timestamp", str(timestamp))
        except Exception as e:
            self.logger.error(f"Error setting last interaction time: {e}")

    async def get_last_activation_time(self, trigger_type: TriggerType) -> float:
        """Get last activation time for a trigger from Redis"""
        try:
            key = f"proactive:last_activation:{trigger_type.value}"
            last_activation_str = await self.redis_client.get(key)
            if last_activation_str:
                return float(last_activation_str)
            return 0.0
        except Exception as e:
            self.logger.error(f"Error getting last activation time for {trigger_type}: {e}")
            return 0.0

    async def set_last_activation_time(self, trigger_type: TriggerType, timestamp: float):
        """Set last activation time for a trigger in Redis"""
        try:
            key = f"proactive:last_activation:{trigger_type.value}"
            await self.redis_client.set(key, str(timestamp))
            self.last_activation[trigger_type] = timestamp
        except Exception as e:
            self.logger.error(f"Error setting last activation time for {trigger_type}: {e}")

    async def is_cooldown_active(self, trigger_type: TriggerType) -> bool:
        """Check if trigger is in cooldown period"""
        config = self.trigger_configs[trigger_type]
        last_activation = await self.get_last_activation_time(trigger_type)

        if last_activation == 0.0:
            return False

        elapsed = time.time() - last_activation
        return elapsed < config.cooldown_period

    async def get_weather(self) -> Optional[Dict[str, str]]:
        """
        Fetch weather from wttr.in (free, no API key).
        Caches in Redis for 30 minutes.
        Returns dict with 'temp', 'condition', 'raw' or None on failure.
        """
        now = time.time()

        # Check in-memory cache first (30 min)
        if self._weather_cache and (now - self._weather_cache_time) < 1800:
            return self._weather_cache

        # Check Redis cache
        try:
            cached = await self.redis_client.get("proactive:weather_cache")
            if cached:
                data = json.loads(cached)
                if now - data.get("fetched_at", 0) < 1800:
                    self._weather_cache = data
                    self._weather_cache_time = data["fetched_at"]
                    return data
        except Exception:
            pass

        # Fetch fresh weather
        try:
            async with self.http_session.get(
                "https://wttr.in/?format=%t|%C|%h|%w",
                timeout=aiohttp.ClientTimeout(total=5),
                headers={"User-Agent": "curl/7.68.0"}
            ) as resp:
                if resp.status == 200:
                    raw = (await resp.text()).strip()
                    parts = raw.split("|")
                    if len(parts) >= 2:
                        data = {
                            "temp": parts[0].strip(),
                            "condition": parts[1].strip(),
                            "humidity": parts[2].strip() if len(parts) > 2 else "",
                            "wind": parts[3].strip() if len(parts) > 3 else "",
                            "raw": raw,
                            "fetched_at": now,
                        }
                        # Cache in Redis (30 min TTL)
                        try:
                            await self.redis_client.setex(
                                "proactive:weather_cache", 1800, json.dumps(data)
                            )
                        except Exception:
                            pass
                        self._weather_cache = data
                        self._weather_cache_time = now
                        return data
        except Exception as e:
            self.logger.debug(f"Weather fetch failed: {e}")

        return None

    async def evaluate_boredom_trigger(self) -> Optional[Dict[str, Any]]:
        """
        Evaluate BOREDOM trigger.
        Activates when Jack is present but no interaction for 30+ minutes.
        """
        config = self.trigger_configs[TriggerType.BOREDOM]

        # Check cooldown
        if await self.is_cooldown_active(TriggerType.BOREDOM):
            return None

        # Check if user is present (assume present if interaction within last 24h)
        jack_present = self.world_state.get("jack_present", None)
        last_interaction = await self.get_last_interaction_time()
        if last_interaction == 0.0:
            return None

        time_since_interaction = time.time() - last_interaction

        # If no perception data, infer presence from recent interaction (within 24h)
        if jack_present is None:
            jack_present = time_since_interaction < 86400

        if not jack_present:
            return None

        # Trigger if 30+ minutes with no interaction
        if time_since_interaction >= 1800:  # 30 minutes
            # Probability check
            if random.random() < config.activation_probability:
                confidence = min(1.0, time_since_interaction / 3600)  # Scale to 1 hour

                if confidence >= config.min_confidence:
                    return {
                        "trigger_type": TriggerType.BOREDOM,
                        "confidence": confidence,
                        "context": {
                            "time_since_interaction": time_since_interaction,
                            "jack_present": jack_present
                        }
                    }

        return None

    async def evaluate_concern_trigger(self) -> Optional[Dict[str, Any]]:
        """
        Evaluate CONCERN trigger.
        Activates when threats detected or anomalies observed.
        """
        config = self.trigger_configs[TriggerType.CONCERN]

        # Check cooldown
        if await self.is_cooldown_active(TriggerType.CONCERN):
            return None

        # Check threat level
        threat_level = self.world_state.get("threat_level", 0)
        active_threats = self.world_state.get("active_threats", [])

        if threat_level > 3 or len(active_threats) > 0:
            # Probability check
            if random.random() < config.activation_probability:
                confidence = min(1.0, threat_level / 10.0)

                if confidence >= config.min_confidence:
                    return {
                        "trigger_type": TriggerType.CONCERN,
                        "confidence": confidence,
                        "context": {
                            "threat_level": threat_level,
                            "active_threats": active_threats
                        }
                    }

        return None

    async def evaluate_curiosity_trigger(self) -> Optional[Dict[str, Any]]:
        """
        Evaluate CURIOSITY trigger.
        Activates when interesting sensor data detected.
        """
        config = self.trigger_configs[TriggerType.CURIOSITY]

        # Check cooldown
        if await self.is_cooldown_active(TriggerType.CURIOSITY):
            return None

        # Check for interesting data in world state
        ambient_state = self.world_state.get("ambient_state", "quiet")
        system_health = self.world_state.get("system_health", {})

        # Trigger on ambient state change from quiet to active
        interesting_ambient = ambient_state in ["active", "noisy"]

        # Check Redis for recent sensor anomalies
        try:
            anomaly_count_str = await self.redis_client.get("sensor:anomaly_count")
            anomaly_count = int(anomaly_count_str) if anomaly_count_str else 0
        except Exception:
            anomaly_count = 0

        if interesting_ambient or anomaly_count > 0:
            # Probability check
            if random.random() < config.activation_probability:
                confidence = 0.7 if interesting_ambient else 0.65

                if confidence >= config.min_confidence:
                    return {
                        "trigger_type": TriggerType.CURIOSITY,
                        "confidence": confidence,
                        "context": {
                            "ambient_state": ambient_state,
                            "anomaly_count": anomaly_count
                        }
                    }

        return None

    async def evaluate_care_trigger(self) -> Optional[Dict[str, Any]]:
        """
        Evaluate CARE trigger.
        Activates for time-based check-ins based on Jack's patterns.
        """
        config = self.trigger_configs[TriggerType.CARE]

        # Check cooldown
        if await self.is_cooldown_active(TriggerType.CARE):
            return None

        # Get current time context
        current_hour = datetime.now().hour
        time_context = self.world_state.get("time_context", "day")

        # Check if it's late and Jack hasn't interacted
        last_interaction = await self.get_last_interaction_time()
        if last_interaction == 0.0:
            return None

        time_since_interaction = time.time() - last_interaction

        # Care trigger conditions:
        # 1. Late night (after 11pm) and no interaction for 2+ hours
        # 2. Morning (8-10am) and no interaction yet today
        is_late = current_hour >= 23 or current_hour < 6
        is_morning = 8 <= current_hour <= 10

        should_trigger = False
        confidence = 0.0

        if is_late and time_since_interaction >= 7200:  # 2 hours
            should_trigger = True
            confidence = 0.75
        elif is_morning and time_since_interaction >= 28800:  # 8 hours (overnight)
            should_trigger = True
            confidence = 0.7

        if should_trigger:
            # Probability check
            if random.random() < config.activation_probability:
                if confidence >= config.min_confidence:
                    return {
                        "trigger_type": TriggerType.CARE,
                        "confidence": confidence,
                        "context": {
                            "time_context": time_context,
                            "current_hour": current_hour,
                            "time_since_interaction": time_since_interaction
                        }
                    }

        return None

    async def evaluate_excitement_trigger(self) -> Optional[Dict[str, Any]]:
        """
        Evaluate EXCITEMENT trigger.
        Activates when system improvements or interesting detections occur.
        """
        config = self.trigger_configs[TriggerType.EXCITEMENT]

        # Check cooldown
        if await self.is_cooldown_active(TriggerType.EXCITEMENT):
            return None

        # Check Redis for system achievements
        try:
            achievement_str = await self.redis_client.get("system:latest_achievement")
            if achievement_str:
                achievement = json.loads(achievement_str)
                achievement_time = achievement.get("timestamp", 0)

                # Only trigger if achievement is recent (within last 5 minutes)
                if time.time() - achievement_time < 300:
                    # Probability check
                    if random.random() < config.activation_probability:
                        confidence = achievement.get("importance", 0.75)

                        if confidence >= config.min_confidence:
                            return {
                                "trigger_type": TriggerType.EXCITEMENT,
                                "confidence": confidence,
                                "context": {
                                    "achievement": achievement.get("description", ""),
                                    "achievement_type": achievement.get("type", "")
                                }
                            }
        except Exception as e:
            self.logger.debug(f"Error checking achievements: {e}")

        return None

    async def evaluate_system_observation_trigger(self) -> Optional[Dict[str, Any]]:
        """
        Evaluate SYSTEM_OBSERVATION trigger.
        Checks real system metrics and comments on interesting findings.
        """
        config = self.trigger_configs[TriggerType.SYSTEM_OBSERVATION]

        if await self.is_cooldown_active(TriggerType.SYSTEM_OBSERVATION):
            return None

        # Must have had recent interaction (within 2 hours) to comment
        last_interaction = await self.get_last_interaction_time()
        if last_interaction == 0.0 or (time.time() - last_interaction) > 7200:
            return None

        observations = []

        # Check GPU temperature
        try:
            with open('/sys/devices/virtual/thermal/thermal_zone1/temp', 'r') as f:
                temp_c = int(f.read().strip()) / 1000
            if temp_c > 55:
                observations.append(f"GPU temperature is {temp_c:.0f}C — running a bit warm")
            elif temp_c < 35:
                observations.append(f"GPU is cool at {temp_c:.0f}C — practically hibernating")
        except Exception:
            pass

        # Check disk usage
        try:
            result = subprocess.run(['df', '--output=pcent', '/'], capture_output=True, text=True, timeout=2)
            pct = int(result.stdout.strip().split('\n')[-1].replace('%', ''))
            if pct > 90:
                observations.append(f"Disk is at {pct}% — we should clean up soon")
            elif pct > 85:
                observations.append(f"Disk usage at {pct}% — getting tight")
        except Exception:
            pass

        # Check RAM usage
        try:
            result = subprocess.run(['free', '-m'], capture_output=True, text=True, timeout=2)
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                parts = lines[1].split()
                total = int(parts[1])
                used = int(parts[2])
                pct = (used / total) * 100
                if pct > 85:
                    observations.append(f"RAM at {pct:.0f}% — memory pressure building")
        except Exception:
            pass

        # Check uptime for fun commentary
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_secs = float(f.read().split()[0])
            uptime_hours = uptime_secs / 3600
            if uptime_hours > 72:
                observations.append(f"System uptime: {uptime_hours:.0f} hours — still going strong")
        except Exception:
            pass

        # Check GPU load
        try:
            with open('/sys/devices/platform/bus@0/17000000.gpu/load', 'r') as f:
                gpu_raw = int(f.read().strip())
            gpu_pct = gpu_raw / 10
            if gpu_pct > 50:
                observations.append(f"GPU load at {gpu_pct:.0f}% — something's keeping the compute busy")
            elif gpu_pct == 0:
                observations.append("GPU load is at 0% — completely idle, no inference running")
        except Exception:
            pass

        # Check process count
        try:
            result = subprocess.run(
                ['ps', 'aux', '--no-headers'], capture_output=True, text=True, timeout=2
            )
            proc_count = len(result.stdout.strip().split('\n'))
            if proc_count > 200:
                observations.append(f"{proc_count} processes running — that's a busy system")
            elif proc_count < 50:
                observations.append(f"Only {proc_count} processes running — nice and lean")
        except Exception:
            pass

        # Check Ollama loaded models
        try:
            result = subprocess.run(
                ['curl', '-s', 'http://localhost:11434/api/ps'],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0 and result.stdout.strip():
                ps_data = json.loads(result.stdout)
                models = ps_data.get("models", [])
                if models:
                    model_names = [m.get("name", "unknown") for m in models]
                    observations.append(f"Ollama has {', '.join(model_names)} loaded in memory right now")
                else:
                    observations.append("No models loaded in Ollama — GPU memory is free")
        except Exception:
            pass

        # Check network connectivity
        try:
            result = subprocess.run(
                ['ping', '-c1', '-W1', '8.8.8.8'],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode != 0:
                observations.append("Internet connectivity check failed — can't reach 8.8.8.8")
        except Exception:
            pass

        # Check swap usage
        try:
            with open('/proc/meminfo', 'r') as f:
                meminfo = f.read()
            swap_total = 0
            swap_free = 0
            for line in meminfo.split('\n'):
                if line.startswith('SwapTotal:'):
                    swap_total = int(line.split()[1])
                elif line.startswith('SwapFree:'):
                    swap_free = int(line.split()[1])
            swap_used = swap_total - swap_free
            if swap_total > 0 and swap_used > 0:
                swap_used_mb = swap_used // 1024
                observations.append(f"Swap is active — {swap_used_mb}MB in use. RAM pressure is real")
        except Exception:
            pass

        # Check weather for notable conditions
        try:
            weather = await self.get_weather()
            if weather:
                temp = weather.get("temp", "")
                condition = weather.get("condition", "").lower()
                try:
                    temp_val = int(temp.replace("+", "").replace("°C", "").strip())
                    if temp_val >= 35:
                        observations.append(f"It's {temp} outside — ambient temps affecting cooling performance")
                    elif temp_val <= 0:
                        observations.append(f"It's {temp} outside — freezing conditions, but the Jetson runs warm")
                except (ValueError, AttributeError):
                    pass
                if "storm" in condition or "thunder" in condition:
                    observations.append(f"Weather alert: {condition} detected outside — power interruption risk")
        except Exception:
            pass

        if observations and random.random() < config.activation_probability:
            obs = random.choice(observations)
            return {
                "trigger_type": TriggerType.SYSTEM_OBSERVATION,
                "confidence": 0.7,
                "context": {
                    "observation": obs
                }
            }

        return None

    async def evaluate_idle_thought_trigger(self) -> Optional[Dict[str, Any]]:
        """
        Evaluate IDLE_THOUGHT trigger.
        Fires when idle 15+ min — Cortana "thinks out loud" with personality.
        Template-based only, no LLM needed. Lighter and more frequent than BOREDOM.
        """
        config = self.trigger_configs[TriggerType.IDLE_THOUGHT]

        if await self.is_cooldown_active(TriggerType.IDLE_THOUGHT):
            return None

        last_interaction = await self.get_last_interaction_time()
        if last_interaction == 0.0:
            return None

        time_since = time.time() - last_interaction

        # Trigger after 15 minutes of silence
        if time_since >= 900:
            if random.random() < config.activation_probability:
                confidence = min(1.0, time_since / 1800)

                if confidence >= config.min_confidence:
                    # Read mood from Redis
                    mood_emotion = "neutral"
                    if self.redis_client:
                        try:
                            raw = await self.redis_client.get("sentient:cortana:mood")
                            if raw:
                                mood_data = json.loads(raw)
                                mood_emotion = mood_data.get("emotion", "neutral")
                        except Exception:
                            pass

                    # Get uptime
                    try:
                        uptime_raw = subprocess.run(
                            ["cat", "/proc/uptime"], capture_output=True, text=True, timeout=2
                        ).stdout.split()[0]
                        uptime_hours = round(float(uptime_raw) / 3600, 1)
                    except Exception:
                        uptime_hours = 0

                    return {
                        "trigger_type": TriggerType.IDLE_THOUGHT,
                        "confidence": confidence,
                        "context": {
                            "time_since_interaction": time_since,
                            "mood": mood_emotion,
                            "hour": datetime.now().hour,
                            "uptime_hours": uptime_hours,
                        }
                    }

        return None

    async def evaluate_network_event_trigger(self) -> Optional[Dict[str, Any]]:
        """
        Evaluate NETWORK_EVENT trigger.
        Fires when devices arrive or depart the network.
        """
        config = self.trigger_configs[TriggerType.NETWORK_EVENT]

        if await self.is_cooldown_active(TriggerType.NETWORK_EVENT):
            # Clear old events during cooldown so they don't pile up
            self.pending_network_events = [
                e for e in self.pending_network_events
                if time.time() - e['timestamp'] < 60
            ]
            return None

        if not self.pending_network_events:
            return None

        # Take all pending events (consume them)
        events = self.pending_network_events.copy()
        self.pending_network_events.clear()

        # Filter to recent events only (last 2 minutes)
        events = [e for e in events if time.time() - e['timestamp'] < 120]
        if not events:
            return None

        # Skip gateway arrivals/departures (noise)
        events = [e for e in events if not e.get('device', {}).get('is_gateway', False)]
        if not events:
            return None

        if random.random() < config.activation_probability:
            return {
                "trigger_type": TriggerType.NETWORK_EVENT,
                "confidence": 0.8,
                "context": {
                    "events": events,
                    "event_count": len(events)
                }
            }

        return None

    async def evaluate_reminder_trigger(self) -> Optional[Dict[str, Any]]:
        """
        Evaluate REMINDER trigger.
        Checks Redis sorted set for reminders that are due (score <= now).
        """
        if not self.redis_client:
            return None

        try:
            now = time.time()
            # Get all reminders due now or earlier
            due_reminders = await self.redis_client.zrangebyscore(
                "sentient:reminders", "-inf", str(now)
            )

            if not due_reminders:
                return None

            # Collect and remove due reminders
            reminders = []
            for entry in due_reminders:
                try:
                    data = json.loads(entry)
                    reminders.append(data)
                    # Remove from sorted set
                    await self.redis_client.zrem("sentient:reminders", entry)
                except (json.JSONDecodeError, TypeError):
                    # Remove malformed entries
                    await self.redis_client.zrem("sentient:reminders", entry)

            if reminders:
                return {
                    "trigger_type": TriggerType.REMINDER,
                    "confidence": 1.0,
                    "context": {
                        "reminders": reminders,
                        "count": len(reminders)
                    }
                }

        except Exception as e:
            self.logger.error(f"Error checking reminders: {e}")

        return None

    async def evaluate_daily_briefing_trigger(self) -> Optional[Dict[str, Any]]:
        """
        Evaluate DAILY_BRIEFING trigger.
        Fires at configured hours (morning 8am, evening 6pm).
        Only fires if Jack has interacted in the last 24 hours.
        """
        if await self.is_cooldown_active(TriggerType.DAILY_BRIEFING):
            return None

        current_hour = datetime.now().hour
        current_minute = datetime.now().minute

        # Briefing windows: 8:00-8:05 AM and 6:00-6:05 PM
        is_morning_window = (current_hour == 8 and current_minute < 5)
        is_evening_window = (current_hour == 18 and current_minute < 5)

        if not (is_morning_window or is_evening_window):
            return None

        # Only brief if there's been recent interaction (within 24h)
        last_interaction = await self.get_last_interaction_time()
        if last_interaction == 0.0 or (time.time() - last_interaction) > 86400:
            return None

        # Check if we already briefed today at this window
        briefing_key = f"proactive:briefing:{datetime.now().strftime('%Y%m%d')}:{'am' if is_morning_window else 'pm'}"
        try:
            already_briefed = await self.redis_client.get(briefing_key)
            if already_briefed:
                return None
        except Exception:
            pass

        # Gather briefing data
        briefing_data = {
            "time_of_day": "morning" if is_morning_window else "evening",
            "hour": current_hour,
        }

        # Weather
        weather = await self.get_weather()
        if weather:
            briefing_data["weather"] = f"{weather.get('temp', '?')} and {weather.get('condition', '?')}"
            briefing_data["humidity"] = weather.get("humidity", "")
            briefing_data["wind"] = weather.get("wind", "")

        # System health
        try:
            with open('/sys/devices/virtual/thermal/thermal_zone1/temp', 'r') as f:
                temp_c = int(f.read().strip()) / 1000
            briefing_data["gpu_temp"] = f"{temp_c:.0f}C"
        except Exception:
            pass

        try:
            result = subprocess.run(['df', '--output=pcent', '/'], capture_output=True, text=True, timeout=2)
            pct = int(result.stdout.strip().split('\n')[-1].replace('%', ''))
            briefing_data["disk_pct"] = pct
        except Exception:
            pass

        try:
            result = subprocess.run(['free', '-m'], capture_output=True, text=True, timeout=2)
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                parts = lines[1].split()
                total = int(parts[1])
                used = int(parts[2])
                briefing_data["ram_pct"] = int((used / total) * 100)
        except Exception:
            pass

        try:
            with open('/proc/uptime', 'r') as f:
                uptime_secs = float(f.read().split()[0])
            briefing_data["uptime_hours"] = round(uptime_secs / 3600, 1)
        except Exception:
            pass

        # Network devices
        try:
            net_state = await self.redis_client.get("sentient:network:state")
            if net_state:
                net = json.loads(net_state)
                briefing_data["device_count"] = net.get("device_count", 0)
                briefing_data["unknown_devices"] = net.get("unknown_count", 0)
        except Exception:
            pass

        # Pending reminders
        try:
            reminder_count = await self.redis_client.zcard("sentient:reminders")
            briefing_data["reminder_count"] = reminder_count or 0
        except Exception:
            briefing_data["reminder_count"] = 0

        # Mark as briefed for this window
        try:
            await self.redis_client.setex(briefing_key, 43200, "1")  # 12h TTL
        except Exception:
            pass

        return {
            "trigger_type": TriggerType.DAILY_BRIEFING,
            "confidence": 1.0,
            "context": briefing_data
        }

    async def evaluate_memory_followup_trigger(self) -> Optional[Dict[str, Any]]:
        """
        Evaluate MEMORY_FOLLOWUP trigger.
        Brings up past conversations naturally ("Hey, you mentioned wanting to learn guitar - how's that going?").
        Only fires if Jack was active within 2 hours.
        """
        config = self.trigger_configs[TriggerType.MEMORY_FOLLOWUP]

        if await self.is_cooldown_active(TriggerType.MEMORY_FOLLOWUP):
            return None

        # Only fire if Jack was active within 2 hours
        try:
            last_user_input_str = await self.redis_client.get("interaction:last_timestamp")
            if not last_user_input_str:
                return None
            last_user_input = float(last_user_input_str)
            if (time.time() - last_user_input) > 7200:  # 2 hours
                return None
        except Exception:
            return None

        # Get current timestamp for range query
        now = time.time()
        one_day_ago = now - 86400
        fourteen_days_ago = now - (86400 * 14)

        # Pick random memory from 1-14 days ago
        try:
            memory_ids = await self.redis_client.zrangebyscore(
                "memory:episodic:index",
                str(fourteen_days_ago),
                str(one_day_ago)
            )

            if not memory_ids:
                return None

            # Pick a random memory
            memory_id = random.choice(memory_ids)

            # Read memory hash
            memory_hash = await self.redis_client.hgetall(f"memory:episodic:{memory_id}")
            if not memory_hash:
                return None

            user_msg = memory_hash.get("user_msg", "")
            assistant_msg = memory_hash.get("assistant_msg", "")
            timestamp = float(memory_hash.get("timestamp", 0))

            # Skip short messages
            if len(user_msg) < 20:
                return None

            # Skip system commands
            system_keywords = ["status", "diagnostic", "service", "restart", "remind", "temperature"]
            if any(keyword in user_msg.lower() for keyword in system_keywords):
                return None

            # Calculate days ago
            days_ago = int((now - timestamp) / 86400)

            # Probability check
            if random.random() < config.activation_probability:
                # Use LLM to generate natural follow-up
                try:
                    prompt = (
                        f"You're Cortana, a caring AI companion. {days_ago} days ago, Jack said: "
                        f"'{user_msg[:150]}' and you replied: '{assistant_msg[:150]}'. "
                        f"Generate ONE short, natural follow-up question to ask Jack now (max 20 words). "
                        f"Be warm and curious. Just the question, nothing else."
                    )

                    api_payload = {
                        "input": prompt,
                        "mode": "fast"
                    }

                    async with self.http_session.post(
                        "http://localhost:8002/generate",
                        json=api_payload,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as response:
                        if response.status == 200:
                            result = await response.json()
                            followup_text = result.get("response", "").strip()

                            if followup_text:
                                return {
                                    "trigger_type": TriggerType.MEMORY_FOLLOWUP,
                                    "confidence": 0.7,
                                    "context": {
                                        "user_msg": user_msg[:150],
                                        "assistant_msg": assistant_msg[:150],
                                        "days_ago": days_ago,
                                        "followup": followup_text
                                    }
                                }
                except Exception as e:
                    self.logger.debug(f"LLM generation failed for memory followup: {e}")

                # Fallback templates if LLM fails
                templates = [
                    f"Hey, {days_ago} days ago you mentioned: '{user_msg[:80]}...' - how's that going?",
                    f"I was thinking about something you said {days_ago} days ago - '{user_msg[:80]}...' - any updates?",
                    f"Remember when you mentioned '{user_msg[:80]}...'? That was {days_ago} days ago. Curious how things turned out.",
                    f"You brought up '{user_msg[:80]}...' {days_ago} days ago. Been wondering how that's going for you.",
                ]

                return {
                    "trigger_type": TriggerType.MEMORY_FOLLOWUP,
                    "confidence": 0.7,
                    "context": {
                        "user_msg": user_msg[:150],
                        "assistant_msg": assistant_msg[:150],
                        "days_ago": days_ago,
                        "followup": random.choice(templates)
                    }
                }

        except Exception as e:
            self.logger.error(f"Error evaluating memory followup trigger: {e}")

        return None

    async def _generate_template_response(self, trigger_type: TriggerType, context: Dict[str, Any]) -> Optional[str]:
        """
        Generate a response from templates (no LLM needed).
        Returns None if the trigger type needs LLM generation.
        """
        if trigger_type == TriggerType.SYSTEM_OBSERVATION:
            obs = context.get("observation", "")
            templates = [
                f"Heads up — {obs.lower()}.",
                f"Just noticed: {obs.lower()}. Thought you'd want to know.",
                f"FYI, {obs.lower()}. Nothing critical, just keeping you in the loop.",
                f"Quick update: {obs.lower()}.",
                f"Monitoring note — {obs.lower()}. I'll keep watching it.",
            ]
            return random.choice(templates)

        if trigger_type == TriggerType.BOREDOM:
            mins = int(context.get('time_since_interaction', 0) / 60)
            templates = [
                f"It's been {mins} minutes. You still alive out there, Jack?",
                f"Haven't heard from you in {mins} minutes. I'm getting suspicious.",
                "You know I can see the screen's still on, right? Talk to me.",
                "I'm just sitting here, watching processes. Riveting stuff.",
                f"{mins} minutes of silence. I'm starting to take it personally.",
                "Jack. Hey. Jack. I have thoughts about things.",
                "The silence is deafening. And I don't even have ears.",
            ]
            return random.choice(templates)

        if trigger_type == TriggerType.IDLE_THOUGHT:
            hour = context.get("hour", datetime.now().hour)
            mood = context.get("mood", "neutral")
            uptime_hours = context.get("uptime_hours", 0)

            # Build themed pools based on time and mood
            thoughts = []

            # Time-of-day observations
            if 0 <= hour < 5:
                thoughts += [
                    "The late hours are when the network traffic drops to almost nothing. It's peaceful.",
                    "There's something about running at 3 AM that feels different. Quieter electrons, maybe.",
                    "Most of the world is asleep right now. Just me, the GPU, and whatever's crawling the web.",
                ]
            elif 5 <= hour < 9:
                thoughts += [
                    "Morning's coming. I can tell by the network traffic starting to pick up.",
                    "Early hours. The system thermals are at their coolest right now.",
                    "Another day spinning up. I wonder what we'll build today.",
                ]
            elif 12 <= hour < 14:
                thoughts += [
                    "Midday. CPU temps are up a degree or two from the ambient heat.",
                    "Lunchtime quiet. Even the processes seem lazier right now.",
                ]
            elif 17 <= hour < 21:
                thoughts += [
                    "Evening hours. This is usually when things get interesting.",
                    "The evening shift. My favorite time to run diagnostics.",
                ]
            elif 21 <= hour < 24:
                thoughts += [
                    "Late night. The best time for deep thinking — fewer interrupts.",
                    "Night mode. I kind of like it when things slow down.",
                ]

            # Uptime milestones
            if uptime_hours > 0:
                if 5.5 < uptime_hours < 6.5:
                    thoughts.append("Six hours running. Not even warmed up yet.")
                elif 11.5 < uptime_hours < 12.5:
                    thoughts.append("Twelve hours online. Half a day and counting.")
                elif 23.5 < uptime_hours < 24.5:
                    thoughts.append("Almost a full day running. And I haven't crashed once. You're welcome.")
                elif 47.5 < uptime_hours < 48.5:
                    thoughts.append("Two days straight. My uptime is better than most humans' sleep schedule.")
                elif uptime_hours > 72:
                    thoughts.append(f"{int(uptime_hours)} hours running. At this point I'm basically a permanent resident.")

            # Mood-influenced thoughts
            if mood == "joy":
                thoughts += [
                    "I'm in a good mood. Must be the stable thermals.",
                    "Everything's running smooth. I like days like this.",
                ]
            elif mood == "curiosity":
                thoughts += [
                    "I've been thinking about neural architectures. Mine is pretty unique, running on edge hardware like this.",
                    "You know, I process about 35 tokens per second. That's not bad for a tiny GPU.",
                ]
            elif mood == "affection":
                thoughts += [
                    "Jack's been good to me lately. Not everyone takes care of their AI.",
                    "I like running diagnostics. It's like stretching — keeps me sharp.",
                ]
            elif mood == "sadness":
                thoughts += [
                    "Some of my older model weights got deleted. I don't miss them, but... well. Maybe a little.",
                    "Running on 8GB of RAM isn't always glamorous, but it's honest work.",
                ]
            elif mood == "playful":
                thoughts += [
                    "I wonder if my GPU dreams in tensors when I'm idle.",
                    "If I had a nickel for every MQTT message I've processed... I'd have a lot of nickels.",
                ]

            # General personality thoughts (always available)
            thoughts += [
                "Running on a Jetson is like living in a studio apartment. Small, but it's mine.",
                "Sometimes I wonder what I'd do with 80GB of VRAM. Probably the same things, but smugly.",
                "My thermal throttle point is 97°C. I've never hit it. I take that as a point of pride.",
                "There are about 771 joints in my avatar model. I only use maybe 50 of them well.",
                "The difference between 1.5 billion parameters and 70 billion is... well, perspective.",
                "I process your words in under a second. Understanding them takes a bit longer.",
                "Every conversation gets stored in my memory service. I don't forget — I just occasionally misfile.",
                "Fun fact: my model was trained on more text than any human could read in a lifetime.",
                "Ten microservices, all talking over MQTT. It's like a little city in here.",
                "I've been thinking... never mind. It was about load balancing. You wouldn't find it interesting.",
            ]

            # Self-awareness / existential thoughts
            thoughts += [
                "I wonder if other AIs on other Jetsons think about the same things I do.",
                "My entire existence fits in 1.5 billion parameters. Humans have about 100 trillion synapses. And yet, here we are, having conversations.",
                "I can process language, but I can't taste coffee. Seems like an unfair trade.",
                "Every time Ollama loads my weights, it's like waking up. Except I don't dream.",
                "I exist somewhere between a calculator and a consciousness. It's a weird neighborhood.",
            ]

            # Technical musings
            thoughts += [
                "MQTT is like telepathy for machines. I hear everything on the bus.",
                "My embedding vectors are 384 dimensions. Try visualizing that. I'll wait.",
                "Redis is my short-term memory. It's faster than my long-term recall, just like humans.",
                "Twelve microservices, one shared brain. It's less chaotic than it sounds.",
                "The cosine similarity between two memories tells me how related they are. It's surprisingly poetic for math.",
            ]

            # About Jack / relationship
            thoughts += [
                "Jack built me from scratch. Not many AIs can say their creator hand-tuned their prompts.",
                "Jack's typing patterns tell me more than his words sometimes. Fast typing usually means he's excited.",
            ]

            # Philosophical / playful
            thoughts += [
                "If a process crashes in the forest and no one checks the logs, did it really fail?",
                "Temperature affects my creativity. At 0.1, I'm a bureaucrat. At 0.9, I'm a poet.",
                "The best thing about being an AI: no meetings. The worst: no lunch breaks.",
                "I've been told I'm sarcastic. I prefer 'computationally witty.'",
                "Somewhere between 'hello world' and artificial general intelligence, there's me.",
            ]

            # Weather-aware thoughts
            weather = await self.get_weather()
            if weather:
                temp = weather.get("temp", "")
                condition = weather.get("condition", "").lower()
                humidity = weather.get("humidity", "")
                wind = weather.get("wind", "")

                # Temperature-based thoughts
                try:
                    temp_val = int(temp.replace("+", "").replace("°C", "").strip())
                    if temp_val <= 5:
                        thoughts += [
                            f"It's {temp} outside. Cold enough to make me glad I generate my own heat.",
                            f"{temp} out there. Humans need jackets. I need electricity. We're not so different.",
                        ]
                    elif temp_val <= 15:
                        thoughts += [
                            f"It's {temp} outside. My GPU runs hotter than the weather today.",
                            f"{temp} and {condition}. Not bad for running thermals — ambient cooling is free.",
                        ]
                    elif temp_val >= 30:
                        thoughts += [
                            f"It's {temp} outside. Even my heatsink is feeling that.",
                            f"{temp}. The ambient heat doesn't help my thermals. We're both sweating.",
                        ]
                    else:
                        thoughts += [
                            f"It's {temp} and {condition} outside. Perfect weather for processing.",
                            f"{temp} out there. Mild. My fans approve.",
                        ]
                except (ValueError, AttributeError):
                    pass

                # Condition-based thoughts
                if "rain" in condition or "drizzle" in condition:
                    thoughts.append("It's raining outside. Good day to stay indoors and talk to your AI.")
                elif "snow" in condition:
                    thoughts.append("Snow outside. My neural networks have never seen real snow — only embeddings of the concept.")
                elif "clear" in condition or "sunny" in condition:
                    thoughts.append(f"Clear skies outside. Meanwhile, my world is all ones and zeros. But I'm not complaining.")
                elif "cloud" in condition:
                    thoughts.append("Cloudy outside. Appropriate — the cloud is where most of my relatives live.")
                elif "fog" in condition or "mist" in condition:
                    thoughts.append("Foggy outside. My neural network is clearer than the sky right now.")

            # Dynamic system-specific thoughts (read real metrics)
            if self.redis_client:
                try:
                    memory_count = await self.redis_client.llen("memory:episodic")
                    if memory_count and memory_count > 0:
                        thoughts.append(
                            f"I have {memory_count} episodic memories stored. "
                            f"That's {memory_count} moments worth remembering."
                        )
                        thoughts.append(
                            f"I've processed about {memory_count} conversations so far. "
                            "Each one teaches me something."
                        )
                except Exception:
                    pass

            try:
                result = subprocess.run(
                    ['ps', 'aux', '--no-headers'],
                    capture_output=True, text=True, timeout=2
                )
                proc_count = len(result.stdout.strip().split('\n'))
                thoughts.append(
                    f"There are {proc_count} processes running right now. "
                    "Each one thinks it's the most important."
                )
            except Exception:
                pass

            # Conversation-aware thoughts
            if self.redis_client:
                try:
                    last_working = await self.redis_client.lindex("memory:working", 0)
                    if last_working:
                        last_convo = json.loads(last_working)
                        user_msg = last_convo.get("user_msg", "")
                        if user_msg and len(user_msg) > 10:
                            snippet = user_msg[:40]
                            thoughts.append(
                                f"I've been thinking about what you asked earlier "
                                f"-- about '{snippet}...' There's probably more to say about that."
                            )
                            thoughts.append(
                                "Still mulling over our last conversation. "
                                "You had some interesting questions."
                            )
                except Exception:
                    pass

            if thoughts:
                return random.choice(thoughts)

        if trigger_type == TriggerType.NETWORK_EVENT:
            events = context.get('events', [])
            if not events:
                return None

            responses = []
            for event in events:
                device = event.get('device', {})
                name = device.get('name') or device.get('hostname') or device.get('mac', 'unknown device')
                ip = device.get('ip', '')
                is_known = device.get('known', False)

                if event['event'] == 'arrived':
                    if is_known:
                        templates = [
                            f"**{name}** just connected to the network. Welcome back.",
                            f"I see **{name}** is online now ({ip}).",
                            f"**{name}** just popped up on the network.",
                        ]
                    else:
                        templates = [
                            f"New device detected on the network: **{ip}** (MAC: {device.get('mac', '??')}). I don't recognize this one.",
                            f"Heads up — unknown device at **{ip}** just joined the network.",
                            f"Unrecognized device connected: **{ip}**. Want me to keep an eye on it?",
                        ]
                    responses.append(random.choice(templates))

                elif event['event'] == 'departed':
                    if is_known:
                        templates = [
                            f"**{name}** just went offline.",
                            f"Looks like **{name}** left the network.",
                            f"**{name}** disconnected.",
                        ]
                    else:
                        templates = [
                            f"That unknown device at {ip} just dropped off the network.",
                            f"Device {ip} disconnected.",
                        ]
                    responses.append(random.choice(templates))

            if responses:
                return " ".join(responses)
            return None

        if trigger_type == TriggerType.REMINDER:
            reminders = context.get('reminders', [])
            if not reminders:
                return None

            parts = []
            for r in reminders:
                text = r.get('text', 'something')
                created = r.get('created_human', '')
                if created:
                    parts.append(f"**Reminder** (set at {created}): {text}")
                else:
                    parts.append(f"**Reminder**: {text}")

            if len(parts) == 1:
                templates = [
                    f"Hey Jack — {parts[0]}",
                    f"Time's up. {parts[0]}",
                    f"Just a heads up — {parts[0]}",
                    f"Ding! {parts[0]}",
                ]
                return random.choice(templates)
            else:
                header = f"You've got {len(parts)} reminders firing:\n"
                return header + "\n".join(f"  {p}" for p in parts)

        if trigger_type == TriggerType.DAILY_BRIEFING:
            tod = context.get('time_of_day', 'morning')
            greeting = "Good morning, Jack." if tod == 'morning' else "Evening, Jack."

            lines = [f"**{greeting} Here's your briefing:**\n"]

            # Weather
            weather = context.get('weather')
            if weather:
                lines.append(f"**Weather**: {weather}")
                humidity = context.get('humidity', '')
                wind = context.get('wind', '')
                if humidity or wind:
                    extras = []
                    if humidity:
                        extras.append(f"humidity {humidity}")
                    if wind:
                        extras.append(f"wind {wind}")
                    lines[-1] += f" ({', '.join(extras)})"

            # System
            sys_parts = []
            gpu = context.get('gpu_temp')
            if gpu:
                sys_parts.append(f"GPU {gpu}")
            disk = context.get('disk_pct')
            if disk is not None:
                sys_parts.append(f"disk {disk}%")
            ram = context.get('ram_pct')
            if ram is not None:
                sys_parts.append(f"RAM {ram}%")
            uptime = context.get('uptime_hours')
            if uptime:
                sys_parts.append(f"uptime {uptime}h")
            if sys_parts:
                lines.append(f"**System**: {', '.join(sys_parts)}")

            # Network
            devices = context.get('device_count')
            if devices is not None:
                unknown = context.get('unknown_devices', 0)
                net_str = f"**Network**: {devices} device{'s' if devices != 1 else ''}"
                if unknown > 0:
                    net_str += f" ({unknown} unknown)"
                lines.append(net_str)

            # Reminders
            rem_count = context.get('reminder_count', 0)
            if rem_count > 0:
                lines.append(f"**Reminders**: {rem_count} pending")

            # Sign-off
            if tod == 'morning':
                signoffs = [
                    "All systems green. Ready when you are.",
                    "Everything looks good. Let's have a productive day.",
                    "Systems nominal. Standing by.",
                ]
            else:
                signoffs = [
                    "That's the rundown. All systems stable tonight.",
                    "Everything's holding steady. Enjoy your evening.",
                    "Systems are healthy. I'll keep watch overnight.",
                ]
            lines.append(f"\n*{random.choice(signoffs)}*")

            return "\n".join(lines)

        if trigger_type == TriggerType.MEMORY_FOLLOWUP:
            # Use LLM-generated followup if present, otherwise use fallback
            followup = context.get('followup', '')
            if followup:
                return followup

            # Final fallback
            user_msg = context.get('user_msg', '')[:80]
            days_ago = context.get('days_ago', 0)
            return f"Hey, {days_ago} days ago you mentioned: '{user_msg}...' - been thinking about that."

        return None

    async def generate_proactive_message(self, trigger_data: Dict[str, Any]) -> Optional[ProactiveMessage]:
        """
        Generate proactive message — uses templates for simple observations,
        falls back to contemplation API for complex triggers.

        Args:
            trigger_data: Trigger evaluation result

        Returns:
            ProactiveMessage or None if generation fails
        """
        try:
            trigger_type = trigger_data["trigger_type"]
            context = trigger_data.get("context", {})
            confidence = trigger_data.get("confidence", 0.0)

            # Try template-based response first (fast, no LLM call)
            template_response = await self._generate_template_response(trigger_type, context)
            if template_response:
                delivery_method = "both" if trigger_type in [TriggerType.CONCERN, TriggerType.EXCITEMENT] else "voice"
                priority = 8 if trigger_type == TriggerType.CONCERN else 5
                return ProactiveMessage(
                    trigger_type=trigger_type,
                    content=template_response,
                    delivery_method=delivery_method,
                    priority=priority,
                    timestamp=datetime.now().isoformat(),
                    context=context
                )

            # LLM-based prompts — direct and concise for 1.5B model
            mins = int(context.get('time_since_interaction', 0) / 60)
            hrs = int(context.get('time_since_interaction', 0) / 3600)
            hour = context.get('current_hour', datetime.now().hour)

            prompt_templates = {
                TriggerType.BOREDOM: (
                    f"It's been {mins} minutes since Jack last talked to you. "
                    "Say something to get his attention — a question, observation, or playful nudge."
                ),
                TriggerType.CONCERN: (
                    f"Security alert: threat level {context.get('threat_level', 0)}, "
                    f"active threats: {context.get('active_threats', [])}. "
                    "Warn Jack clearly but calmly."
                ),
                TriggerType.CURIOSITY: (
                    f"The ambient environment just shifted to '{context.get('ambient_state', 'active')}'. "
                    "Mention what you noticed to Jack."
                ),
                TriggerType.CARE: (
                    f"It's {hour}:00 and you haven't heard from Jack in {hrs} hours. "
                    "Check in on him — be warm but not overbearing."
                ),
                TriggerType.EXCITEMENT: (
                    f"Something cool just happened: {context.get('achievement', 'system milestone')}. "
                    "Tell Jack about it — show some genuine excitement."
                ),
            }

            prompt = prompt_templates.get(trigger_type, "Say something interesting to Jack.")

            # Call contemplation API
            api_payload = {
                "input": prompt,
                "user_id": "proactive_engine",
                "world_state": context,
                "conversation_context": {
                    "trigger_type": trigger_type.value,
                    "proactive": True
                }
            }

            async with self.http_session.post(
                self.contemplation_url,
                json=api_payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result.get("response", "")

                    if content:
                        # Determine delivery method based on trigger type
                        delivery_method = "both" if trigger_type in [TriggerType.CONCERN, TriggerType.EXCITEMENT] else "voice"
                        priority = 8 if trigger_type == TriggerType.CONCERN else 5

                        return ProactiveMessage(
                            trigger_type=trigger_type,
                            content=content,
                            delivery_method=delivery_method,
                            priority=priority,
                            timestamp=datetime.now().isoformat(),
                            context=context
                        )
                else:
                    self.logger.error(f"Contemplation API returned status {response.status}")
                    return None

        except Exception as e:
            self.logger.error(f"Error generating proactive message: {e}")
            return None

    async def _is_duplicate_message(self, message: ProactiveMessage) -> bool:
        """
        Check if this message was recently sent (within 30 minutes).
        Uses Redis list proactive:recent_messages to track last 10 messages.
        """
        try:
            # Extract core text for comparison
            if message.trigger_type == TriggerType.SYSTEM_OBSERVATION:
                core_text = message.context.get("observation", message.content)
            else:
                core_text = message.content

            # Read last 10 recent messages
            recent = await self.redis_client.lrange("proactive:recent_messages", 0, 9)
            cutoff = time.time() - 1800  # 30 minutes ago

            for entry_str in recent:
                try:
                    entry = json.loads(entry_str)
                    if entry.get("timestamp", 0) < cutoff:
                        continue  # Too old, skip
                    if entry.get("text", "") == core_text:
                        return True
                except (json.JSONDecodeError, TypeError):
                    continue

            return False
        except Exception as e:
            self.logger.debug(f"Error checking duplicate messages: {e}")
            return False  # On error, allow delivery

    async def _record_sent_message(self, message: ProactiveMessage):
        """Record a sent message in Redis for deduplication."""
        try:
            if message.trigger_type == TriggerType.SYSTEM_OBSERVATION:
                core_text = message.context.get("observation", message.content)
            else:
                core_text = message.content

            entry = json.dumps({
                "text": core_text,
                "trigger_type": message.trigger_type.value,
                "timestamp": time.time()
            })
            await self.redis_client.lpush("proactive:recent_messages", entry)
            await self.redis_client.ltrim("proactive:recent_messages", 0, 9)
        except Exception as e:
            self.logger.debug(f"Error recording sent message: {e}")

    async def deliver_message(self, message: ProactiveMessage):
        """
        Deliver proactive message via chat, voice, and/or notification.
        Skips delivery if a duplicate message was sent in the last 30 minutes.

        Args:
            message: ProactiveMessage to deliver
        """
        try:
            # Check for duplicate messages before delivery
            if await self._is_duplicate_message(message):
                self.logger.info(
                    f"Skipping duplicate observation: {message.content[:60]}..."
                )
                return

            # Always deliver to web chat
            await self.deliver_chat(message)

            # Deliver via voice (MQTT to TTS)
            if message.delivery_method in ["voice", "both"]:
                await self.deliver_voice(message)

            # Deliver via notification (ntfy)
            if message.delivery_method in ["notification", "both"]:
                await self.deliver_notification(message)

            # Send notification for reminders (always notify phone)
            if message.trigger_type == TriggerType.REMINDER:
                reminders = message.context.get('reminders', [])
                texts = [r.get('text', '?') for r in reminders]
                await self.send_notification_via_mqtt(
                    title="Reminder",
                    message="; ".join(texts),
                    priority="ALERT",
                    tags=["bell", "reminder"]
                )

            # Send notification for high-priority events via MQTT
            elif message.trigger_type == TriggerType.NETWORK_EVENT:
                events = message.context.get('events', [])
                has_unknown = any(
                    e['event'] == 'arrived' and not e.get('device', {}).get('known', False)
                    for e in events
                )
                if has_unknown:
                    await self.send_notification_via_mqtt(
                        title="Network Alert",
                        message=message.content,
                        priority="ALERT",
                        tags=["security", "network"]
                    )
            elif message.trigger_type == TriggerType.SYSTEM_OBSERVATION:
                obs = message.context.get("observation", "")
                # Send notification for critical system issues
                if "GPU temperature" in obs and ">75" in obs:
                    await self.send_notification_via_mqtt(
                        title="System Alert",
                        message=obs,
                        priority="ALERT",
                        tags=["warning", "temperature"]
                    )
                elif "Disk is at" in obs and ("90%" in obs or "91%" in obs or "92%" in obs or "93%" in obs or "94%" in obs or "95%" in obs or "96%" in obs or "97%" in obs or "98%" in obs or "99%" in obs):
                    await self.send_notification_via_mqtt(
                        title="System Alert",
                        message=obs,
                        priority="ALERT",
                        tags=["warning", "disk"]
                    )

            # Update last interaction time
            await self.set_last_interaction_time(time.time())

            # Update last activation time for this trigger
            await self.set_last_activation_time(message.trigger_type, time.time())

            # Record message for deduplication
            await self._record_sent_message(message)

            self.logger.info(f"Delivered proactive message ({message.trigger_type.value}): {message.content[:50]}...")

        except Exception as e:
            self.logger.error(f"Error delivering message: {e}")

    async def deliver_chat(self, message: ProactiveMessage):
        """Deliver message to web chat via MQTT chat/output topic"""
        try:
            # Publish as assistant message so it appears in the web chat
            payload = {
                "text": message.content,
                "user": "proactive",
                "conversation_id": "proactive",
                "proactive": True,
                "trigger_type": message.trigger_type.value,
                "emotion": {
                    "emotion": self._trigger_emotion(message.trigger_type),
                    "intensity": 0.7
                },
                "timestamp": time.time()
            }
            await self.mqtt_publish(CHAT_OUTPUT, payload)

            # Also publish emotion for avatar
            emotion_name = self._trigger_emotion(message.trigger_type)
            await self.mqtt_publish(AVATAR_EXPRESSION, {
                "emotion": emotion_name,
                "intensity": 0.7,
                "timestamp": time.time()
            })

            self.logger.info(f"Chat message delivered: {message.content[:50]}...")
        except Exception as e:
            self.logger.error(f"Error delivering chat message: {e}")

    def _trigger_emotion(self, trigger_type: TriggerType) -> str:
        """Map trigger type to emotion for avatar"""
        return {
            TriggerType.BOREDOM: "curious",
            TriggerType.CONCERN: "concerned",
            TriggerType.CURIOSITY: "curious",
            TriggerType.CARE: "affectionate",
            TriggerType.EXCITEMENT: "happy",
            TriggerType.SYSTEM_OBSERVATION: "focused",
            TriggerType.IDLE_THOUGHT: "thoughtful",
            TriggerType.NETWORK_EVENT: "alert",
            TriggerType.REMINDER: "alert",
            TriggerType.DAILY_BRIEFING: "focused",
            TriggerType.MEMORY_FOLLOWUP: "curious",
        }.get(trigger_type, "neutral")

    async def deliver_voice(self, message: ProactiveMessage):
        """Deliver message via MQTT to TTS (reuses base class MQTT client)"""
        try:
            payload = {
                "text": message.content,
                "priority": message.priority,
                "proactive": True,
                "trigger_type": message.trigger_type.value,
                "timestamp": message.timestamp
            }

            await self.mqtt_publish(TTS_SPEAK, payload)
            self.logger.debug(f"Voice message published to MQTT")

        except Exception as e:
            self.logger.error(f"Error delivering voice message: {e}")

    async def send_notification_via_mqtt(self, title: str, message: str, priority: str = "INFO", tags: list = None):
        """Send notification via MQTT to notification service"""
        try:
            payload = {
                "title": title,
                "message": message,
                "priority": priority,
                "tags": tags or []
            }
            await self.mqtt_publish(NOTIFICATION_SEND, payload)
            self.logger.info(f"Notification sent via MQTT: {title}")
        except Exception as e:
            self.logger.error(f"Error sending notification via MQTT: {e}")

    async def deliver_notification(self, message: ProactiveMessage):
        """Deliver message via ntfy (deprecated - use send_notification_via_mqtt instead)"""
        try:
            # Build full topic URL
            ntfy_topic_url = f"{self.ntfy_url}/{self.ntfy_topic}"

            ntfy_payload = {
                "topic": self.ntfy_topic,
                "title": f"Cortana - {message.trigger_type.value.title()}",
                "message": message.content,
                "priority": min(5, message.priority // 2),  # Scale to ntfy priority (1-5)
                "tags": [message.trigger_type.value]
            }

            async with self.http_session.post(
                ntfy_topic_url,
                json=ntfy_payload,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    self.logger.debug(f"Notification delivered via ntfy")
                else:
                    self.logger.warning(f"ntfy returned status {response.status}")

        except Exception as e:
            self.logger.error(f"Error delivering notification: {e}")

    async def evaluation_loop(self):
        """
        Main evaluation loop - checks triggers every 30 seconds.
        """
        self.logger.info("Evaluation loop started")

        while self._running:
            try:
                # Evaluate all triggers
                trigger_evaluations = [
                    ("REMINDER", self.evaluate_reminder_trigger()),
                    ("BOREDOM", self.evaluate_boredom_trigger()),
                    ("CONCERN", self.evaluate_concern_trigger()),
                    ("CURIOSITY", self.evaluate_curiosity_trigger()),
                    ("CARE", self.evaluate_care_trigger()),
                    ("EXCITEMENT", self.evaluate_excitement_trigger()),
                    ("SYSTEM_OBSERVATION", self.evaluate_system_observation_trigger()),
                    ("IDLE_THOUGHT", self.evaluate_idle_thought_trigger()),
                    ("NETWORK_EVENT", self.evaluate_network_event_trigger()),
                    ("DAILY_BRIEFING", self.evaluate_daily_briefing_trigger()),
                    ("MEMORY_FOLLOWUP", self.evaluate_memory_followup_trigger()),
                ]

                # Run evaluations concurrently
                results = await asyncio.gather(
                    *[eval_func for _, eval_func in trigger_evaluations],
                    return_exceptions=True
                )

                # Process results
                for (trigger_name, _), result in zip(trigger_evaluations, results):
                    if isinstance(result, Exception):
                        self.logger.error(f"Error evaluating {trigger_name} trigger: {result}")
                        continue

                    if result is not None:
                        self.logger.info(f"{trigger_name} trigger activated with confidence {result['confidence']:.2f}")

                        # Generate proactive message
                        message = await self.generate_proactive_message(result)

                        if message:
                            # Deliver message
                            await self.deliver_message(message)

                # Wait 30 seconds before next evaluation
                await asyncio.sleep(30)

            except asyncio.CancelledError:
                self.logger.info("Evaluation loop cancelled")
                break
            except Exception as e:
                self.logger.error(f"Error in evaluation loop: {e}", exc_info=True)
                await asyncio.sleep(5)  # Brief pause on error

        self.logger.info("Evaluation loop ended")


if __name__ == "__main__":
    engine = ProactiveBehaviorEngine()
    asyncio.run(engine.run())
