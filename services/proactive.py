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
import logging
import time
import random
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from enum import Enum

import redis.asyncio as redis
import aiomqtt
import aiohttp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TriggerType(str, Enum):
    """Types of proactive triggers"""
    BOREDOM = "boredom"
    CONCERN = "concern"
    CURIOSITY = "curiosity"
    CARE = "care"
    EXCITEMENT = "excitement"


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


class ProactiveBehaviorEngine:
    """
    Autonomous proactive behavior engine.
    Evaluates triggers and initiates interactions naturally.
    """

    def __init__(
        self,
        redis_host: str = "localhost",
        redis_port: int = 6379,
        mqtt_broker: str = "localhost",
        mqtt_port: int = 1883,
        contemplation_api_url: str = "http://localhost:5001/api/contemplation/generate",
        ntfy_url: str = "http://localhost:8082",
        ntfy_topic: str = "cortana-proactive"
    ):
        """Initialize proactive behavior engine"""
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.contemplation_api_url = contemplation_api_url
        self.ntfy_url = ntfy_url
        self.ntfy_topic = ntfy_topic

        # Redis client
        self.redis_client: Optional[redis.Redis] = None

        # MQTT client
        self.mqtt_client: Optional[aiomqtt.Client] = None

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
                cooldown_period=1800,  # 30 min cooldown
                activation_probability=0.4,  # 40% chance when triggered
                min_confidence=0.7
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
                cooldown_period=900,  # 15 min cooldown
                activation_probability=0.5,  # 50% chance when triggered
                min_confidence=0.65
            ),
            TriggerType.CARE: TriggerConfig(
                trigger_type=TriggerType.CARE,
                evaluation_interval=300,  # Check every 5 min
                cooldown_period=3600,  # 1 hour cooldown
                activation_probability=0.6,  # 60% chance when triggered
                min_confidence=0.7
            ),
            TriggerType.EXCITEMENT: TriggerConfig(
                trigger_type=TriggerType.EXCITEMENT,
                evaluation_interval=60,  # Check every minute
                cooldown_period=600,  # 10 min cooldown
                activation_probability=0.7,  # 70% chance when triggered
                min_confidence=0.75
            )
        }

        # Last trigger activation times (for cooldown tracking)
        self.last_activation: Dict[TriggerType, float] = {}

        # Evaluation loop control
        self.running: bool = False
        self.evaluation_task: Optional[asyncio.Task] = None
        self.world_state_task: Optional[asyncio.Task] = None

        logger.info("ProactiveBehaviorEngine initialized")

    async def connect(self):
        """Connect to Redis and MQTT"""
        # Connect to Redis
        try:
            self.redis_client = await redis.from_url(
                f"redis://{self.redis_host}:{self.redis_port}",
                decode_responses=True
            )
            await self.redis_client.ping()
            logger.info(f"✓ Connected to Redis at {self.redis_host}:{self.redis_port}")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

        # Create HTTP session
        self.http_session = aiohttp.ClientSession()
        logger.info("✓ HTTP session created")

        logger.info("All connections established")

    async def disconnect(self):
        """Disconnect from all services"""
        if self.redis_client:
            await self.redis_client.close()
            logger.info("Redis connection closed")

        if self.http_session:
            await self.http_session.close()
            logger.info("HTTP session closed")

    async def subscribe_world_state(self):
        """Subscribe to world state updates via MQTT"""
        try:
            async with aiomqtt.Client(
                hostname=self.mqtt_broker,
                port=self.mqtt_port
            ) as client:
                await client.subscribe("sentient/world/state")
                logger.info("Subscribed to sentient/world/state")

                async for message in client.messages:
                    try:
                        payload = json.loads(message.payload.decode())
                        self.world_state = payload
                        self.last_world_state_update = time.time()
                        logger.debug(f"World state updated: jack_present={payload.get('jack_present')}")
                    except Exception as e:
                        logger.error(f"Error processing world state: {e}")

        except Exception as e:
            logger.error(f"Error in world state subscription: {e}")
            # Retry connection after delay
            await asyncio.sleep(5)
            if self.running:
                asyncio.create_task(self.subscribe_world_state())

    async def get_last_interaction_time(self) -> float:
        """Get timestamp of last interaction from Redis"""
        try:
            last_interaction_str = await self.redis_client.get("interaction:last_timestamp")
            if last_interaction_str:
                return float(last_interaction_str)
            return 0.0
        except Exception as e:
            logger.error(f"Error getting last interaction time: {e}")
            return 0.0

    async def set_last_interaction_time(self, timestamp: float):
        """Set timestamp of last interaction in Redis"""
        try:
            await self.redis_client.set("interaction:last_timestamp", str(timestamp))
        except Exception as e:
            logger.error(f"Error setting last interaction time: {e}")

    async def get_last_activation_time(self, trigger_type: TriggerType) -> float:
        """Get last activation time for a trigger from Redis"""
        try:
            key = f"proactive:last_activation:{trigger_type.value}"
            last_activation_str = await self.redis_client.get(key)
            if last_activation_str:
                return float(last_activation_str)
            return 0.0
        except Exception as e:
            logger.error(f"Error getting last activation time for {trigger_type}: {e}")
            return 0.0

    async def set_last_activation_time(self, trigger_type: TriggerType, timestamp: float):
        """Set last activation time for a trigger in Redis"""
        try:
            key = f"proactive:last_activation:{trigger_type.value}"
            await self.redis_client.set(key, str(timestamp))
            self.last_activation[trigger_type] = timestamp
        except Exception as e:
            logger.error(f"Error setting last activation time for {trigger_type}: {e}")

    async def is_cooldown_active(self, trigger_type: TriggerType) -> bool:
        """Check if trigger is in cooldown period"""
        config = self.trigger_configs[trigger_type]
        last_activation = await self.get_last_activation_time(trigger_type)

        if last_activation == 0.0:
            return False

        elapsed = time.time() - last_activation
        return elapsed < config.cooldown_period

    async def evaluate_boredom_trigger(self) -> Optional[Dict[str, Any]]:
        """
        Evaluate BOREDOM trigger.
        Activates when Jack is present but no interaction for 30+ minutes.
        """
        config = self.trigger_configs[TriggerType.BOREDOM]

        # Check cooldown
        if await self.is_cooldown_active(TriggerType.BOREDOM):
            return None

        # Check if Jack is present
        jack_present = self.world_state.get("jack_present", False)
        if not jack_present:
            return None

        # Check last interaction time
        last_interaction = await self.get_last_interaction_time()
        if last_interaction == 0.0:
            return None

        time_since_interaction = time.time() - last_interaction

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
        except:
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
        except:
            pass

        return None

    async def generate_proactive_message(self, trigger_data: Dict[str, Any]) -> Optional[ProactiveMessage]:
        """
        Generate proactive message via contemplation API.

        Args:
            trigger_data: Trigger evaluation result

        Returns:
            ProactiveMessage or None if generation fails
        """
        try:
            trigger_type = trigger_data["trigger_type"]
            context = trigger_data.get("context", {})
            confidence = trigger_data.get("confidence", 0.0)

            # Build prompt for contemplation API
            prompt_templates = {
                TriggerType.BOREDOM: (
                    f"You haven't heard from Jack in a while (about {int(context.get('time_since_interaction', 0) / 60)} minutes). "
                    "Generate a casual, natural comment or question to initiate conversation. "
                    "Be friendly and genuine, not needy. Examples: 'Hey, been thinking about...', 'Quick question...'"
                ),
                TriggerType.CONCERN: (
                    f"Threat level is {context.get('threat_level', 0)}. Active threats: {context.get('active_threats', [])}. "
                    "Alert Jack naturally about the situation. Be clear but not alarmist."
                ),
                TriggerType.CURIOSITY: (
                    f"Noticed something interesting: ambient state is {context.get('ambient_state', 'unknown')}. "
                    "Share your observation with Jack. Start with 'Hey, I noticed...' or similar."
                ),
                TriggerType.CARE: (
                    f"It's {context.get('current_hour', 0)}:00 and haven't heard from Jack in {int(context.get('time_since_interaction', 0) / 3600)} hours. "
                    "Do a caring check-in. Be warm and genuine, show you care without being intrusive."
                ),
                TriggerType.EXCITEMENT: (
                    f"Something exciting happened: {context.get('achievement', 'system improvement detected')}. "
                    "Share your enthusiasm with Jack! Be genuinely excited and positive."
                )
            }

            prompt = prompt_templates.get(trigger_type, "Generate a natural proactive message.")

            # Call contemplation API
            api_payload = {
                "trigger_type": trigger_type.value,
                "prompt": prompt,
                "context": context,
                "max_length": 200
            }

            async with self.http_session.post(
                self.contemplation_api_url,
                json=api_payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result.get("message", "")

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
                    logger.error(f"Contemplation API returned status {response.status}")
                    return None

        except Exception as e:
            logger.error(f"Error generating proactive message: {e}")
            return None

    async def deliver_message(self, message: ProactiveMessage):
        """
        Deliver proactive message via voice (MQTT) and/or notification (ntfy).

        Args:
            message: ProactiveMessage to deliver
        """
        try:
            # Deliver via voice (MQTT to TTS)
            if message.delivery_method in ["voice", "both"]:
                await self.deliver_voice(message)

            # Deliver via notification (ntfy)
            if message.delivery_method in ["notification", "both"]:
                await self.deliver_notification(message)

            # Update last interaction time
            await self.set_last_interaction_time(time.time())

            # Update last activation time for this trigger
            await self.set_last_activation_time(message.trigger_type, time.time())

            logger.info(f"Delivered proactive message ({message.trigger_type.value}): {message.content[:50]}...")

        except Exception as e:
            logger.error(f"Error delivering message: {e}")

    async def deliver_voice(self, message: ProactiveMessage):
        """Deliver message via MQTT to TTS"""
        try:
            async with aiomqtt.Client(
                hostname=self.mqtt_broker,
                port=self.mqtt_port
            ) as client:
                payload = {
                    "text": message.content,
                    "priority": message.priority,
                    "proactive": True,
                    "trigger_type": message.trigger_type.value,
                    "timestamp": message.timestamp
                }

                await client.publish(
                    "sentient/voice/tts/input",
                    json.dumps(payload).encode()
                )

                logger.debug(f"Voice message published to MQTT")

        except Exception as e:
            logger.error(f"Error delivering voice message: {e}")

    async def deliver_notification(self, message: ProactiveMessage):
        """Deliver message via ntfy"""
        try:
            ntfy_payload = {
                "topic": self.ntfy_topic,
                "title": f"Cortana - {message.trigger_type.value.title()}",
                "message": message.content,
                "priority": min(5, message.priority // 2),  # Scale to ntfy priority (1-5)
                "tags": [message.trigger_type.value]
            }

            async with self.http_session.post(
                self.ntfy_url,
                json=ntfy_payload,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    logger.debug(f"Notification delivered via ntfy")
                else:
                    logger.warning(f"ntfy returned status {response.status}")

        except Exception as e:
            logger.error(f"Error delivering notification: {e}")

    async def evaluation_loop(self):
        """
        Main evaluation loop - checks triggers every 30 seconds.
        """
        logger.info("Evaluation loop started")

        while self.running:
            try:
                # Evaluate all triggers
                trigger_evaluations = [
                    ("BOREDOM", self.evaluate_boredom_trigger()),
                    ("CONCERN", self.evaluate_concern_trigger()),
                    ("CURIOSITY", self.evaluate_curiosity_trigger()),
                    ("CARE", self.evaluate_care_trigger()),
                    ("EXCITEMENT", self.evaluate_excitement_trigger())
                ]

                # Run evaluations concurrently
                results = await asyncio.gather(
                    *[eval_func for _, eval_func in trigger_evaluations],
                    return_exceptions=True
                )

                # Process results
                for (trigger_name, _), result in zip(trigger_evaluations, results):
                    if isinstance(result, Exception):
                        logger.error(f"Error evaluating {trigger_name} trigger: {result}")
                        continue

                    if result is not None:
                        logger.info(f"{trigger_name} trigger activated with confidence {result['confidence']:.2f}")

                        # Generate proactive message
                        message = await self.generate_proactive_message(result)

                        if message:
                            # Deliver message
                            await self.deliver_message(message)

                # Wait 30 seconds before next evaluation
                await asyncio.sleep(30)

            except asyncio.CancelledError:
                logger.info("Evaluation loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in evaluation loop: {e}", exc_info=True)
                await asyncio.sleep(5)  # Brief pause on error

        logger.info("Evaluation loop ended")

    async def start(self):
        """Start the proactive behavior engine"""
        logger.info("Starting ProactiveBehaviorEngine...")

        # Connect to services
        await self.connect()

        # Start background tasks
        self.running = True
        self.world_state_task = asyncio.create_task(self.subscribe_world_state())
        self.evaluation_task = asyncio.create_task(self.evaluation_loop())

        logger.info("✓ ProactiveBehaviorEngine running")

    async def stop(self):
        """Stop the proactive behavior engine"""
        logger.info("Stopping ProactiveBehaviorEngine...")

        self.running = False

        # Cancel background tasks
        if self.evaluation_task:
            self.evaluation_task.cancel()
            try:
                await self.evaluation_task
            except asyncio.CancelledError:
                pass

        if self.world_state_task:
            self.world_state_task.cancel()
            try:
                await self.world_state_task
            except asyncio.CancelledError:
                pass

        # Disconnect from services
        await self.disconnect()

        logger.info("ProactiveBehaviorEngine stopped")


async def main():
    """Main entry point"""
    import signal
    import sys

    # Configure from environment
    import os

    config = {
        "redis_host": os.getenv("REDIS_HOST", "localhost"),
        "redis_port": int(os.getenv("REDIS_PORT", "6379")),
        "mqtt_broker": os.getenv("MQTT_BROKER", "localhost"),
        "mqtt_port": int(os.getenv("MQTT_PORT", "1883")),
        "contemplation_api_url": os.getenv(
            "CONTEMPLATION_API_URL",
            "http://localhost:5001/api/contemplation/generate"
        ),
        "ntfy_url": os.getenv("NTFY_URL", "http://localhost:8082"),
        "ntfy_topic": os.getenv("NTFY_TOPIC", "cortana-proactive")
    }

    # Create engine
    engine = ProactiveBehaviorEngine(**config)

    # Signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        asyncio.create_task(engine.stop())
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Start engine
        await engine.start()

        # Keep running
        while engine.running:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        await engine.stop()


if __name__ == "__main__":
    asyncio.run(main())
