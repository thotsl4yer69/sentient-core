"""
Perception Layer Service - Unified World State Aggregation

Subscribes to all sensor inputs and system status, aggregates data into
a unified world state, and publishes every 5 seconds.

Inputs:
- sentient/sensor/vision/*/detection (vision detections from pi1)
- sentient/sensor/rf/detection (RF detections from ESP32)
- sentient/system/status (node health)
- Audio environment analysis (via PyAudio)
- Time awareness (time of day, day of week, last interaction)

Outputs:
- sentient/world/state (unified world state every 5s)
"""

import asyncio
import json
import struct
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from enum import Enum

import aiomqtt
import pyaudio
import redis.asyncio as redis

from sentient.config import get_config
from sentient.common.logging import setup_logging
from sentient.common import mqtt_topics
from sentient.services.perception.network_scanner import NetworkScanner

# Setup logging
logger = setup_logging("perception")


class AmbientState(str, Enum):
    """Ambient environment classification"""
    QUIET = "quiet"
    ACTIVE = "active"
    NOISY = "noisy"


class TimeContext(str, Enum):
    """Time of day classification"""
    MORNING = "morning"      # 6am-12pm
    AFTERNOON = "afternoon"  # 12pm-6pm
    EVENING = "evening"      # 6pm-10pm
    NIGHT = "night"         # 10pm-6am


@dataclass
class Threat:
    """Threat detection"""
    source: str
    type: str
    severity: int
    timestamp: str
    location: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


@dataclass
class WorldState:
    """Unified world state representation"""
    timestamp: str
    jack_present: bool
    jack_location: Optional[str]
    threat_level: int  # 0-10
    active_threats: List[Dict[str, Any]]
    ambient_state: str
    time_context: str
    last_interaction_seconds: int
    system_health: Dict[str, Any]


class AudioMonitor:
    """Real-time audio level monitoring using PyAudio"""

    def __init__(
        self,
        rate: int = 44100,
        chunk_size: int = 1024,
        channels: int = 1,
        format: int = pyaudio.paInt16
    ):
        self.rate = rate
        self.chunk_size = chunk_size
        self.channels = channels
        self.format = format

        self.pyaudio = pyaudio.PyAudio()
        self.stream: Optional[Any] = None
        self.current_level: float = 0.0
        self.running: bool = False

        logger.info("AudioMonitor initialized")

    async def start(self):
        """Start audio monitoring"""
        try:
            self.stream = self.pyaudio.open(
                format=self.format,
                channels=self.channels,
                rate=self.rate,
                input=True,
                frames_per_buffer=self.chunk_size,
                stream_callback=self._audio_callback
            )
            self.stream.start_stream()
            self.running = True
            logger.info("Audio monitoring started")
        except Exception as e:
            logger.error(f"Failed to start audio monitoring: {e}")
            self.current_level = 0.0

    def _audio_callback(self, in_data, frame_count, time_info, status):
        """PyAudio callback for processing audio chunks"""
        try:
            # Convert bytes to numpy-like array
            audio_data = struct.unpack(f"{frame_count}h", in_data)

            # Calculate RMS amplitude
            sum_squares = sum(sample ** 2 for sample in audio_data)
            rms = (sum_squares / frame_count) ** 0.5

            # Normalize to 0-100 scale (32768 is max for int16)
            self.current_level = min(100, (rms / 32768.0) * 100)

        except Exception as e:
            logger.error(f"Error processing audio: {e}")

        return (in_data, pyaudio.paContinue)

    def get_level(self) -> float:
        """Get current audio level (0-100)"""
        return self.current_level

    def get_ambient_state(self) -> AmbientState:
        """Classify ambient state based on audio level"""
        level = self.current_level

        if level < 5:
            return AmbientState.QUIET
        elif level < 30:
            return AmbientState.ACTIVE
        else:
            return AmbientState.NOISY

    async def stop(self):
        """Stop audio monitoring"""
        self.running = False
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        self.pyaudio.terminate()
        logger.info("Audio monitoring stopped")


class TimeAwareness:
    """Time context and interaction tracking"""

    def __init__(self):
        self.last_interaction_time: Optional[datetime] = None

    def get_time_context(self) -> TimeContext:
        """Get current time of day context"""
        hour = datetime.now().hour

        if 6 <= hour < 12:
            return TimeContext.MORNING
        elif 12 <= hour < 18:
            return TimeContext.AFTERNOON
        elif 18 <= hour < 22:
            return TimeContext.EVENING
        else:
            return TimeContext.NIGHT

    def update_interaction(self):
        """Update last interaction timestamp"""
        self.last_interaction_time = datetime.now()
        logger.debug("Interaction timestamp updated")

    def get_seconds_since_interaction(self) -> int:
        """Get seconds since last interaction"""
        if self.last_interaction_time is None:
            return -1  # No interaction yet

        delta = datetime.now() - self.last_interaction_time
        return int(delta.total_seconds())


class PerceptionLayer:
    """
    Main perception layer that aggregates all sensor data
    into unified world state
    """

    def __init__(
        self,
        mqtt_broker: Optional[str] = None,
        mqtt_port: Optional[int] = None,
        mqtt_username: Optional[str] = None,
        mqtt_password: Optional[str] = None,
        publish_interval: Optional[float] = None
    ):
        # Load central config
        config = get_config()

        # Use provided values or fall back to config
        self.mqtt_broker = mqtt_broker or config.mqtt.broker
        self.mqtt_port = mqtt_port or config.mqtt.port
        self.mqtt_username = mqtt_username or config.mqtt.username
        self.mqtt_password = mqtt_password or config.mqtt.password
        self.publish_interval = publish_interval or config.perception.world_state_interval

        # State storage
        self.vision_detections: Dict[str, Any] = {}
        self.rf_detections: Dict[str, Any] = {}
        self.system_status: Dict[str, Any] = {}
        self.active_threats: List[Threat] = []

        # Components
        self.audio_monitor = AudioMonitor()
        self.time_awareness = TimeAwareness()

        # Runtime flags
        self.running = False
        self._last_published_state: Optional[Dict[str, Any]] = None
        self._last_publish_time: float = 0.0
        self._heartbeat_interval: float = 30.0  # Force publish every 30s even if unchanged

        # Network scanner
        self.network_scanner: Optional[NetworkScanner] = None
        self.redis_client: Optional[redis.Redis] = None

        logger.info(f"PerceptionLayer initialized for {self.mqtt_broker}:{self.mqtt_port}")

    async def handle_vision_detection(self, topic: str, payload: dict):
        """Handle vision detection messages"""
        try:
            # Extract camera ID from topic: sentient/sensor/vision/{camera_id}/detection
            parts = topic.split('/')
            camera_id = parts[3] if len(parts) > 3 else "unknown"

            logger.debug(f"Vision detection from {camera_id}: {payload}")

            self.vision_detections[camera_id] = {
                "timestamp": datetime.now().isoformat(),
                "data": payload
            }

            # Check for Jack's presence
            if "person" in payload.get("classes", []):
                if payload.get("confidence", 0) > 0.7:
                    self.time_awareness.update_interaction()

            # Check for threats
            self._analyze_vision_threats(camera_id, payload)

        except Exception as e:
            logger.error(f"Error handling vision detection: {e}")

    async def handle_rf_detection(self, topic: str, payload: dict):
        """Handle RF detection messages"""
        try:
            logger.debug(f"RF detection: {payload}")

            self.rf_detections = {
                "timestamp": datetime.now().isoformat(),
                "data": payload
            }

            # Known device detection might indicate Jack's presence
            if payload.get("known_device"):
                self.time_awareness.update_interaction()

            # Check for RF-based threats
            self._analyze_rf_threats(payload)

        except Exception as e:
            logger.error(f"Error handling RF detection: {e}")

    async def handle_system_status(self, topic: str, payload: dict):
        """Handle system status messages"""
        try:
            logger.debug(f"System status: {payload}")

            node_id = payload.get("node_id", "unknown")
            self.system_status[node_id] = {
                "timestamp": datetime.now().isoformat(),
                "data": payload
            }

        except Exception as e:
            logger.error(f"Error handling system status: {e}")

    def _analyze_vision_threats(self, camera_id: str, detection: dict):
        """Analyze vision detections for threats"""
        try:
            # Check for unknown persons
            if "unknown_person" in detection.get("classes", []):
                confidence = detection.get("confidence", 0)
                if confidence > 0.8:
                    threat = Threat(
                        source=f"vision_{camera_id}",
                        type="unknown_person",
                        severity=7,
                        timestamp=datetime.now().isoformat(),
                        location=detection.get("location"),
                        details={"confidence": confidence}
                    )
                    self._add_threat(threat)

            # Check for suspicious objects - with confidence-based severity
            suspicious_classes = ["weapon", "tool", "suspicious_object"]
            detected_suspicious = [
                cls for cls in detection.get("classes", [])
                if cls in suspicious_classes
            ]

            if detected_suspicious:
                confidence = detection.get("confidence", 0)
                # Filter out low-confidence false positives
                if confidence < 0.5:
                    logger.debug(f"Ignoring low-confidence suspicious detection: {detected_suspicious} ({confidence:.2f})")
                else:
                    # Scale severity by confidence
                    severity = 8 if confidence >= 0.7 else 5
                    threat = Threat(
                        source=f"vision_{camera_id}",
                        type="suspicious_object",
                        severity=severity,
                        timestamp=datetime.now().isoformat(),
                        location=detection.get("location"),
                        details={"objects": detected_suspicious, "confidence": confidence}
                    )
                    self._add_threat(threat)

        except Exception as e:
            logger.error(f"Error analyzing vision threats: {e}")

    def _analyze_rf_threats(self, detection: dict):
        """Analyze RF detections for threats"""
        try:
            # Unknown RF devices
            if not detection.get("known_device"):
                signal_strength = detection.get("rssi", 0)

                # Strong signal from unknown device
                if signal_strength > -50:
                    threat = Threat(
                        source="rf",
                        type="unknown_rf_device",
                        severity=5,
                        timestamp=datetime.now().isoformat(),
                        details={
                            "rssi": signal_strength,
                            "mac": detection.get("mac_address")
                        }
                    )
                    self._add_threat(threat)

            # RF jamming detection
            if detection.get("jamming_detected"):
                threat = Threat(
                    source="rf",
                    type="rf_jamming",
                    severity=9,
                    timestamp=datetime.now().isoformat()
                )
                self._add_threat(threat)

        except Exception as e:
            logger.error(f"Error analyzing RF threats: {e}")

    def _add_threat(self, threat: Threat):
        """Add threat to active threats list"""
        # Remove old threats (older than 60 seconds)
        cutoff = (datetime.now() - timedelta(seconds=60)).isoformat()
        self.active_threats = [
            t for t in self.active_threats
            if t.timestamp > cutoff
        ]

        # Add new threat
        self.active_threats.append(threat)
        logger.warning(f"Threat detected: {threat.type} (severity: {threat.severity})")

    def _calculate_threat_level(self) -> int:
        """Calculate overall threat level (0-10)"""
        if not self.active_threats:
            return 0

        # Weighted average of threat severities
        total_severity = sum(t.severity for t in self.active_threats)
        max_severity = max(t.severity for t in self.active_threats)

        # Blend average and max
        threat_level = int((total_severity / len(self.active_threats) * 0.6) + (max_severity * 0.4))

        return min(10, threat_level)

    def _determine_jack_presence(self) -> tuple[bool, Optional[str]]:
        """Determine if Jack is present and his location"""
        # Check RF detections for Jack's known devices
        if self.rf_detections:
            rf_data = self.rf_detections.get("data", {})
            if rf_data.get("known_device") and rf_data.get("owner") == "jack":
                return True, rf_data.get("location", "unknown")

        # Check vision detections for Jack
        for camera_id, detection in self.vision_detections.items():
            data = detection.get("data", {})
            if "jack" in data.get("classes", []) or data.get("person_id") == "jack":
                return True, data.get("location") or camera_id

        # Check interaction recency
        seconds_since = self.time_awareness.get_seconds_since_interaction()
        if 0 <= seconds_since < 300:  # Within 5 minutes
            return True, None

        return False, None

    def _aggregate_system_health(self) -> Dict[str, Any]:
        """Aggregate system health from all nodes"""
        health = {}

        for node_id, status in self.system_status.items():
            data = status.get("data", {})
            health[node_id] = {
                "online": data.get("online", False),
                "cpu_percent": data.get("cpu_percent"),
                "memory_percent": data.get("memory_percent"),
                "temperature": data.get("temperature"),
                "uptime": data.get("uptime")
            }

        return health

    def build_world_state(self) -> WorldState:
        """Build unified world state from all sensor data"""
        # Expire stale threats (older than 60 seconds)
        cutoff = (datetime.now() - timedelta(seconds=60)).isoformat()
        self.active_threats = [
            t for t in self.active_threats
            if t.timestamp > cutoff
        ]

        jack_present, jack_location = self._determine_jack_presence()

        # Add network info to system health
        system_health = self._aggregate_system_health()
        if self.network_scanner and self.network_scanner.devices:
            system_health['network'] = {
                'device_count': len(self.network_scanner.devices),
                'known_count': sum(1 for d in self.network_scanner.devices.values() if d.known),
                'summary': self.network_scanner.get_summary()
            }

        world_state = WorldState(
            timestamp=datetime.now().isoformat(),
            jack_present=jack_present,
            jack_location=jack_location,
            threat_level=self._calculate_threat_level(),
            active_threats=[asdict(t) for t in self.active_threats],
            ambient_state=self.audio_monitor.get_ambient_state().value,
            time_context=self.time_awareness.get_time_context().value,
            last_interaction_seconds=self.time_awareness.get_seconds_since_interaction(),
            system_health=system_health
        )

        return world_state

    async def publish_world_state(self, client: aiomqtt.Client):
        """Publish world state to MQTT (only if changed or heartbeat due)"""
        try:
            world_state = self.build_world_state()
            state_dict = asdict(world_state)

            # Check if state actually changed (ignore volatile fields)
            _volatile = {'timestamp', 'last_interaction_seconds', 'system_health'}
            comparable = {k: v for k, v in state_dict.items() if k not in _volatile}
            last_comparable = {k: v for k, v in (self._last_published_state or {}).items() if k not in _volatile} if self._last_published_state else None

            now = time.time()
            heartbeat_due = (now - self._last_publish_time) >= self._heartbeat_interval
            state_changed = (comparable != last_comparable)

            if not state_changed and not heartbeat_due:
                return  # Skip — nothing new

            await client.publish(
                mqtt_topics.PERCEPTION_STATE,
                payload=json.dumps(state_dict),
                qos=1
            )

            self._last_published_state = state_dict
            self._last_publish_time = now

            if state_changed:
                logger.info(
                    f"World state published - Jack: {world_state.jack_present}, "
                    f"Threat: {world_state.threat_level}, "
                    f"Ambient: {world_state.ambient_state}"
                )
            else:
                logger.debug("World state heartbeat (no change)")

        except Exception as e:
            logger.error(f"Error publishing world state: {e}")

    async def publish_loop(self, client: aiomqtt.Client):
        """Publish world state at regular intervals"""
        while self.running:
            try:
                await self.publish_world_state(client)
                await asyncio.sleep(self.publish_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in publish loop: {e}")
                await asyncio.sleep(1)

    async def _init_network_scanner(self):
        """Initialize network scanner with Redis connection"""
        try:
            config = get_config()
            self.redis_client = await redis.from_url(
                f"redis://{config.redis.host}:{config.redis.port}",
                decode_responses=True
            )
            await self.redis_client.ping()
            self.network_scanner = NetworkScanner(
                redis_client=self.redis_client,
                scan_interval=30.0
            )
            await self.network_scanner.initialize()
            logger.info("Network scanner initialized")
        except Exception as e:
            logger.error(f"Failed to initialize network scanner: {e}")

    async def network_scan_loop(self, client: aiomqtt.Client):
        """Run network scans at regular intervals and publish results"""
        if not self.network_scanner:
            return

        while self.running:
            try:
                result = await self.network_scanner.scan()

                # Publish network state
                await client.publish(
                    mqtt_topics.PERCEPTION_STATE.replace('/state', '/network'),
                    payload=json.dumps(result),
                    qos=0
                )

                # Publish individual device events for arrivals/departures
                for device in result.get('arrivals', []):
                    await client.publish(
                        "sentient/network/device/arrived",
                        payload=json.dumps(device),
                        qos=1
                    )

                for device in result.get('departures', []):
                    await client.publish(
                        "sentient/network/device/departed",
                        payload=json.dumps(device),
                        qos=1
                    )

                await asyncio.sleep(self.network_scanner.scan_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Network scan error: {e}")
                await asyncio.sleep(30)

    async def run(self):
        """Main run loop"""
        self.running = True

        # Start audio monitoring
        await self.audio_monitor.start()

        # Initialize network scanner
        await self._init_network_scanner()

        # MQTT connection parameters
        mqtt_params = {
            "hostname": self.mqtt_broker,
            "port": self.mqtt_port
        }

        if self.mqtt_username and self.mqtt_password:
            mqtt_params["username"] = self.mqtt_username
            mqtt_params["password"] = self.mqtt_password

        reconnect_interval = 5

        while self.running:
            try:
                async with aiomqtt.Client(**mqtt_params) as client:
                    logger.info(f"Connected to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}")

                    # Subscribe to all topics using canonical topic constants
                    await client.subscribe("sentient/sensor/vision/+/detection")
                    await client.subscribe(mqtt_topics.RF_DETECTION)
                    await client.subscribe(mqtt_topics.SYSTEM_STATUS)

                    logger.info("Subscribed to all perception topics")

                    # Start publish loop
                    publish_task = asyncio.create_task(self.publish_loop(client))

                    # Start network scan loop
                    network_task = asyncio.create_task(self.network_scan_loop(client))

                    # Message handling loop
                    async for message in client.messages:
                        try:
                            topic = message.topic.value
                            payload = json.loads(message.payload.decode())

                            # Route to appropriate handler
                            if topic.startswith("sentient/sensor/vision/"):
                                await self.handle_vision_detection(topic, payload)
                            elif topic == mqtt_topics.RF_DETECTION:
                                await self.handle_rf_detection(topic, payload)
                            elif topic == mqtt_topics.SYSTEM_STATUS:
                                await self.handle_system_status(topic, payload)

                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to decode message on {message.topic}: {e}")
                        except Exception as e:
                            logger.error(f"Error processing message: {e}")

                    # Clean up publish task
                    publish_task.cancel()
                    try:
                        await publish_task
                    except asyncio.CancelledError:
                        pass

                    # Clean up network scan task
                    network_task.cancel()
                    try:
                        await network_task
                    except asyncio.CancelledError:
                        pass

            except aiomqtt.MqttError as e:
                logger.error(f"MQTT error: {e}. Reconnecting in {reconnect_interval}s...")
                await asyncio.sleep(reconnect_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error: {e}. Reconnecting in {reconnect_interval}s...")
                await asyncio.sleep(reconnect_interval)

    async def stop(self):
        """Stop the perception layer"""
        logger.info("Stopping perception layer...")
        self.running = False
        await self.audio_monitor.stop()
        if self.redis_client:
            await self.redis_client.close()
