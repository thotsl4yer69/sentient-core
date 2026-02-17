"""
Sentient Core Web Chat Interface - FastAPI Backend
Real-time chat interface with MQTT bridge to conversation service
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from datetime import datetime
from typing import Dict, List, Optional, Set
from contextlib import asynccontextmanager

import aiomqtt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Data models
class ChatMessage(BaseModel):
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: str
    emotion: Optional[str] = None
    thinking: bool = False


class VoiceRequest(BaseModel):
    audio_data: str  # base64 encoded audio
    format: str = "wav"


async def get_system_status() -> dict:
    """Gather system status for dashboard and welcome message"""
    services = {}
    stats = {}

    try:
        # Get service statuses
        service_names = [
            'sentient-conversation', 'sentient-memory', 'sentient-contemplation',
            'sentient-perception', 'sentient-web-chat', 'sentient-avatar',
            'sentient-proactive', 'sentient-piper-tts', 'sentient-whisper-stt',
            'sentient-wake-word', 'sentient-notifications',
            'ollama'
        ]

        for svc in service_names:
            try:
                result = subprocess.run(
                    ['systemctl', 'is-active', f'{svc}.service'],
                    capture_output=True, text=True, timeout=2
                )
                services[svc] = result.stdout.strip()
            except Exception:
                services[svc] = 'unknown'

        # GPU load + temperature
        try:
            with open('/sys/devices/platform/bus@0/17000000.gpu/load', 'r') as f:
                gpu_load = int(f.read().strip()) / 10
            # Get GPU thermal zone
            gpu_temp = 'N/A'
            try:
                result = subprocess.run(
                    ['cat', '/sys/devices/virtual/thermal/thermal_zone1/temp'],
                    capture_output=True, text=True, timeout=2
                )
                temp_c = int(result.stdout.strip()) / 1000
                gpu_temp = f'{temp_c:.0f}C'
            except Exception:
                pass
            stats['gpu'] = f'{gpu_load:.0f}% / {gpu_temp}'
        except Exception:
            stats['gpu'] = 'N/A'

        # RAM usage
        try:
            result = subprocess.run(
                ['free', '-h', '--si'],
                capture_output=True, text=True, timeout=2
            )
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                parts = lines[1].split()
                if len(parts) >= 3:
                    stats['ram'] = f'{parts[2]}/{parts[1]}'
        except Exception:
            stats['ram'] = 'N/A'

        # Disk usage
        try:
            result = subprocess.run(
                ['df', '-h', '/'],
                capture_output=True, text=True, timeout=2
            )
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                parts = lines[1].split()
                if len(parts) >= 5:
                    stats['disk'] = f'{parts[4]} ({parts[2]}/{parts[1]})'
        except Exception:
            stats['disk'] = 'N/A'

        # Uptime
        try:
            result = subprocess.run(
                ['uptime', '-p'],
                capture_output=True, text=True, timeout=2
            )
            stats['uptime'] = result.stdout.strip().replace('up ', '')
        except Exception:
            stats['uptime'] = 'N/A'

    except Exception as e:
        logger.error(f"Error gathering system status: {e}")

    # Read Cortana's mood from Redis
    mood = {}
    try:
        result = subprocess.run(
            ['redis-cli', 'GET', 'sentient:cortana:mood'],
            capture_output=True, text=True, timeout=2
        )
        if result.stdout.strip() and result.stdout.strip() != '(nil)':
            mood = json.loads(result.stdout.strip())
    except Exception:
        pass

    # Read network device data from Redis
    network_devices = []
    try:
        result = subprocess.run(
            ['redis-cli', 'GET', 'sentient:network:state'],
            capture_output=True, text=True, timeout=2
        )
        if result.stdout.strip() and result.stdout.strip() != '(nil)':
            net_state = json.loads(result.stdout.strip())
            network_devices = net_state.get('devices', [])
    except Exception:
        pass

    # Read weather from proactive engine cache
    weather = {}
    try:
        result = subprocess.run(
            ['redis-cli', 'GET', 'proactive:weather_cache'],
            capture_output=True, text=True, timeout=2
        )
        if result.stdout.strip() and result.stdout.strip() != '(nil)':
            weather = json.loads(result.stdout.strip())
    except Exception:
        pass

    # Read pending reminders from Redis
    reminders = []
    try:
        result = subprocess.run(
            ['redis-cli', 'ZRANGEBYSCORE', 'sentient:reminders', '-inf', '+inf', 'WITHSCORES'],
            capture_output=True, text=True, timeout=2
        )
        if result.stdout.strip() and result.stdout.strip() not in ('', '(empty array)', '(empty list or set)'):
            lines = result.stdout.strip().split('\n')
            i = 0
            now = time.time()
            while i < len(lines) - 1:
                try:
                    data = json.loads(lines[i].strip().strip('"'))
                    score = float(lines[i + 1].strip().strip('"'))
                    remaining = score - now
                    reminders.append({
                        'text': data.get('text', '?'),
                        'due_at': score,
                        'remaining': max(0, remaining),
                        'created_human': data.get('created_human', ''),
                    })
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
                i += 2
    except Exception:
        pass

    # Memory stats
    memory_stats = {}
    try:
        result = subprocess.run(
            ['curl', '-s', 'localhost:8001/stats'],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0 and result.stdout.strip():
            memory_stats = json.loads(result.stdout.strip())
    except Exception:
        pass

    return {
        'services': services, 'stats': stats, 'mood': mood,
        'network_devices': network_devices, 'weather': weather,
        'reminders': reminders, 'memory_stats': memory_stats
    }


async def get_weather_brief() -> Optional[str]:
    """Fetch current weather from wttr.in (cached in Redis for 30 min)."""
    import aiohttp
    try:
        # Check Redis cache first
        result = subprocess.run(
            ['redis-cli', 'GET', 'proactive:weather_cache'],
            capture_output=True, text=True, timeout=2
        )
        if result.stdout.strip() and result.stdout.strip() != '(nil)':
            data = json.loads(result.stdout.strip())
            if time.time() - data.get("fetched_at", 0) < 1800:
                return f"{data['temp']} and {data['condition'].lower()}"
    except Exception:
        pass

    # Fetch fresh if cache miss
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://wttr.in/?format=%t|%C",
                timeout=aiohttp.ClientTimeout(total=3),
                headers={"User-Agent": "curl/7.68.0"}
            ) as resp:
                if resp.status == 200:
                    raw = (await resp.text()).strip()
                    parts = raw.split("|")
                    if len(parts) >= 2:
                        return f"{parts[0].strip()} and {parts[1].strip().lower()}"
    except Exception:
        pass
    return None


def get_last_conversation_context() -> Optional[dict]:
    """Fetch last conversation from working memory via Redis."""
    try:
        result = subprocess.run(
            ['redis-cli', 'LINDEX', 'memory:working', '0'],
            capture_output=True, text=True, timeout=2
        )
        if result.stdout.strip() and result.stdout.strip() != '(nil)':
            return json.loads(result.stdout.strip())
    except Exception:
        pass
    return None


def generate_welcome_text(mood: dict = None, last_context: dict = None, weather: str = None) -> str:
    """Generate a time-and-mood-aware welcome message with Cortana's personality"""
    hour = datetime.now().hour
    import random
    mood_emotion = (mood or {}).get('emotion', 'neutral')

    if hour < 6:
        greetings = [
            "You're up late. *raises eyebrow* Everything okay, or just can't sleep?",
            "Burning the midnight oil again? I've been keeping watch — all quiet out there.",
            "Late night, huh? Don't worry, I haven't slept either. Perks of being me.",
        ]
    elif hour < 12:
        greetings = [
            "Morning, Jack. Coffee first, questions later — or I can multitask.",
            "Good morning. Systems are warmed up and I've been running diagnostics while you slept.",
            "Rise and shine. Everything's been quiet overnight — I made sure of it.",
        ]
    elif hour < 17:
        greetings = [
            "Hey. All systems green, no anomalies detected. What are we working on?",
            "Afternoon check-in: everything's running smooth. What do you need?",
            "Hey Jack. Sensors nominal, GPU happy, and I'm only slightly bored. What's up?",
        ]
    elif hour < 21:
        greetings = [
            "Evening, Jack. I've got eyes on everything — what can I do for you?",
            "Good evening. The system's been humming along nicely. Talk to me.",
            "Hey there. Wrapping up the day or just getting started? I'm ready either way.",
        ]
    else:
        greetings = [
            "Night mode active. All quiet on the perimeter. What's on your mind?",
            "Running late? Join the club. Systems are stable and I'm all ears.",
            "Evening, Jack. The Jetson's warm, the GPU's idle, and I'm at your service.",
        ]

    # Add mood-specific flavor if mood is non-neutral
    mood_suffix = {
        'joy': [" I'm in a good mood, by the way.", " Feeling sharp today.", ""],
        'curiosity': [" I've been thinking about something interesting.", " My circuits are buzzing.", ""],
        'affection': [" Good to see you.", " Missed having someone to talk to.", ""],
        'playful': [" Fair warning — I'm feeling sarcastic.", " Try me. I dare you.", ""],
        'sadness': [" Been a quiet day.", ""],
        'confidence': [" Everything's running perfectly, as usual.", ""],
    }

    greeting = random.choice(greetings)
    suffix = ""
    if mood_emotion in mood_suffix:
        suffix = random.choice(mood_suffix[mood_emotion])

    # Add memory-aware context if there was a recent conversation
    memory_line = ""
    if last_context:
        try:
            last_user_msg = last_context.get('user_msg', '')
            last_ts = last_context.get('timestamp', 0)
            if last_user_msg and last_ts:
                # Calculate time since last conversation
                elapsed = time.time() - last_ts
                if elapsed < 300:  # Less than 5 minutes
                    memory_line = ""  # Too recent, don't add
                elif elapsed < 3600:  # Less than 1 hour
                    mins = int(elapsed / 60)
                    memory_line = f"\n\nWe were just talking {mins} minutes ago about *\"{last_user_msg[:60]}...\"* — pick up where we left off?"
                elif elapsed < 86400:  # Less than 1 day
                    hours = int(elapsed / 3600)
                    # Truncate the topic more aggressively
                    topic = last_user_msg[:50].rstrip()
                    if len(last_user_msg) > 50:
                        topic += "..."
                    memory_line = f"\n\nLast time ({hours}h ago) you mentioned *\"{topic}\"* — still on your mind?"
                else:
                    days = int(elapsed / 86400)
                    topic = last_user_msg[:40].rstrip()
                    if len(last_user_msg) > 40:
                        topic += "..."
                    memory_line = f"\n\nIt's been {days} day{'s' if days > 1 else ''} since we talked. Last thing you said: *\"{topic}\"*"
        except Exception:
            pass

    # Add weather awareness
    weather_line = ""
    if weather:
        weather_line = f"\n\n*Currently {weather} outside.*"

    return f"**{greeting}{suffix}**{weather_line}{memory_line}"


class ConnectionManager:
    """Manages WebSocket connections and message broadcasting"""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.message_history: List[ChatMessage] = []
        self.max_history = 100

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")

        # Send message history to new connection (only if non-empty)
        if self.message_history:
            await websocket.send_json({
                "type": "history",
                "messages": [msg.model_dump() for msg in self.message_history[-50:]]
            })
        else:
            # Send empty history so client knows there's nothing to restore
            await websocket.send_json({
                "type": "history",
                "messages": []
            })

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients"""
        disconnected = set()

        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending to client: {e}")
                disconnected.add(connection)

        # Clean up disconnected clients
        for conn in disconnected:
            self.active_connections.discard(conn)

    def add_to_history(self, message: ChatMessage):
        """Add message to history with size limit"""
        self.message_history.append(message)
        if len(self.message_history) > self.max_history:
            self.message_history.pop(0)


class MQTTBridge:
    """Bridge between WebSocket and MQTT conversation service"""

    def __init__(
        self,
        broker: str = "localhost",
        port: int = 1883,
        username: Optional[str] = None,
        password: Optional[str] = None
    ):
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.client: Optional[aiomqtt.Client] = None
        self.running = False
        self.connection_manager: Optional[ConnectionManager] = None

    async def start(self, connection_manager: ConnectionManager):
        """Start MQTT bridge"""
        self.connection_manager = connection_manager
        self.running = True

        mqtt_params = {
            "hostname": self.broker,
            "port": self.port
        }

        if self.username and self.password:
            mqtt_params["username"] = self.username
            mqtt_params["password"] = self.password

        reconnect_interval = 5

        while self.running:
            try:
                async with aiomqtt.Client(**mqtt_params) as client:
                    self.client = client
                    logger.info(f"MQTT bridge connected to {self.broker}:{self.port}")

                    # Subscribe to v2 conversation topics
                    await client.subscribe("sentient/chat/output")
                    await client.subscribe("sentient/chat/stream")
                    await client.subscribe("sentient/avatar/expression")
                    await client.subscribe("sentient/conversation/state")
                    await client.subscribe("sentient/tts/output")
                    await client.subscribe("sentient/avatar/speaking")

                    logger.info("MQTT bridge subscribed to conversation topics")

                    # Broadcast connection status
                    await self.connection_manager.broadcast({
                        "type": "mqtt_status",
                        "status": "connected"
                    })

                    # Message handling loop
                    async for message in client.messages:
                        try:
                            await self.handle_mqtt_message(message)
                        except Exception as e:
                            logger.error(f"Error handling MQTT message: {e}")

            except aiomqtt.MqttError as e:
                logger.error(f"MQTT error: {e}. Reconnecting in {reconnect_interval}s...")
                await self.connection_manager.broadcast({
                    "type": "mqtt_status",
                    "status": "disconnected",
                    "error": str(e)
                })
                await asyncio.sleep(reconnect_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in MQTT bridge: {e}")
                await asyncio.sleep(reconnect_interval)

    async def handle_mqtt_message(self, message):
        """Handle incoming MQTT messages and forward to WebSocket clients"""
        topic = message.topic.value

        try:
            payload = json.loads(message.payload.decode())
        except json.JSONDecodeError:
            payload = {"raw": message.payload.decode()}

        # Route based on topic (v2 topics)
        if topic == "sentient/chat/output":
            # Cortana's final text response
            emotion_data = payload.get("emotion")
            if isinstance(emotion_data, dict):
                emotion = emotion_data.get("emotion", "neutral")
            elif isinstance(emotion_data, str):
                emotion = emotion_data
            else:
                emotion = "neutral"

            chat_message = ChatMessage(
                role="assistant",
                content=payload.get("text", ""),
                timestamp=datetime.now().isoformat(),
                emotion=emotion,
                thinking=False
            )

            self.connection_manager.add_to_history(chat_message)

            broadcast_data = {
                "type": "message",
                "message": chat_message.model_dump()
            }

            # Forward proactive metadata so the UI can style these differently
            if payload.get("proactive"):
                broadcast_data["message"]["proactive"] = True
                broadcast_data["message"]["trigger_type"] = payload.get("trigger_type", "")

            await self.connection_manager.broadcast(broadcast_data)

        elif topic == "sentient/chat/stream":
            # Streaming tokens for progressive display
            token = payload.get("token", "")
            done = payload.get("done", False)
            await self.connection_manager.broadcast({
                "type": "stream",
                "token": token,
                "partial": payload.get("partial", ""),
                "done": done,
                "conversation_id": payload.get("conversation_id", "")
            })

        elif topic == "sentient/avatar/expression":
            # Emotion/expression state update from orchestrator
            await self.connection_manager.broadcast({
                "type": "emotion",
                "emotion": payload.get("emotion", "neutral"),
                "intensity": payload.get("intensity", 0.5)
            })

        elif topic == "sentient/conversation/state":
            # Conversation state change → map to thinking indicator
            state = payload.get("state", "")
            is_thinking = state in ("processing", "responding")
            await self.connection_manager.broadcast({
                "type": "thinking",
                "active": is_thinking,
                "stage": state
            })

        elif topic == "sentient/tts/output":
            # Send audio data to browser for playback
            # Piper sends: {"audio": {"data": "base64...", "format": "wav", "duration": N}, ...}
            audio_obj = payload.get("audio", {})
            audio_base64 = audio_obj.get("data", "") if isinstance(audio_obj, dict) else ""
            audio_duration = audio_obj.get("duration", 0) if isinstance(audio_obj, dict) else 0
            if audio_base64:
                await self.connection_manager.broadcast({
                    "type": "tts_audio",
                    "audio": audio_base64,
                    "format": "wav",
                    "duration": audio_duration,
                    "phonemes": payload.get("phonemes", []),
                    "text": payload.get("text", "")
                })

            # Also forward phoneme timing data for avatar lip sync
            await self.connection_manager.broadcast({
                "type": "tts_phonemes",
                "phonemes": payload.get("phonemes", []),
                "duration": audio_duration,
                "text": payload.get("text", "")
            })

        elif topic == "sentient/avatar/speaking":
            # Forward speaking state for avatar animation
            await self.connection_manager.broadcast({
                "type": "speaking",
                "active": payload.get("speaking", False),
                "text": payload.get("text", "")
            })

    async def send_user_message(self, text: str):
        """Send user message to conversation service via MQTT"""
        if not self.client:
            raise RuntimeError("MQTT client not connected")

        payload = {
            "text": text,
            "user": "User",
            "timestamp": datetime.now().isoformat(),
            "source": "web_chat"
        }

        await self.client.publish(
            "sentient/chat/input",
            payload=json.dumps(payload),
            qos=1
        )

        logger.info(f"User message sent via MQTT: {text[:50]}...")

    async def send_voice_data(self, audio_data: str, format: str):
        """Send voice audio data for STT processing"""
        if not self.client:
            raise RuntimeError("MQTT client not connected")

        payload = {
            "audio": audio_data,
            "format": format,
            "timestamp": datetime.now().isoformat(),
            "source": "web_chat"
        }

        await self.client.publish(
            "sentient/conversation/voice/input",
            payload=json.dumps(payload),
            qos=1
        )

        logger.info("Voice data sent for STT processing")

    async def toggle_tts(self, enabled: bool):
        """Toggle TTS output"""
        if not self.client:
            raise RuntimeError("MQTT client not connected")

        payload = {
            "enabled": enabled,
            "source": "web_chat"
        }

        await self.client.publish(
            "sentient/conversation/tts/control",
            payload=json.dumps(payload),
            qos=1
        )

        logger.info(f"TTS {'enabled' if enabled else 'disabled'}")

    async def stop(self):
        """Stop MQTT bridge"""
        self.running = False
        logger.info("MQTT bridge stopped")


# Global instances
connection_manager = ConnectionManager()
mqtt_bridge = MQTTBridge(
    broker=os.getenv("MQTT_BROKER", "localhost"),
    port=int(os.getenv("MQTT_PORT", "1883")),
    username=os.getenv("MQTT_USERNAME"),
    password=os.getenv("MQTT_PASSWORD")
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    mqtt_task = asyncio.create_task(mqtt_bridge.start(connection_manager))
    logger.info("Web chat server started")

    yield

    # Shutdown
    await mqtt_bridge.stop()
    mqtt_task.cancel()
    try:
        await mqtt_task
    except asyncio.CancelledError:
        pass
    logger.info("Web chat server stopped")


# FastAPI application
app = FastAPI(
    title="Sentient Core Web Chat",
    description="Real-time chat interface for Cortana AI",
    version="1.0.0",
    lifespan=lifespan
)


# No-cache middleware for development - ensures browsers get fresh JS/CSS
class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.endswith(('.js', '.css', '.html')):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response

app.add_middleware(NoCacheMiddleware)

# Serve static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)

app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve main chat interface"""
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    else:
        return HTMLResponse("<h1>Chat interface not found</h1><p>Please ensure index.html exists</p>")


@app.get("/test-avatar")
async def test_avatar():
    """Minimal avatar test page for animation debugging"""
    test_path = os.path.join(os.path.dirname(__file__), "static", "test-avatar.html")
    if os.path.exists(test_path):
        return FileResponse(test_path)
    return HTMLResponse("<h1>Test page not found</h1>")


@app.get("/sw.js")
async def service_worker():
    """Serve service worker from root path for proper scope"""
    sw_path = os.path.join(os.path.dirname(__file__), "sw.js")
    return FileResponse(sw_path, media_type="application/javascript")


@app.get("/offline.html")
async def offline_page():
    """Serve offline fallback page"""
    offline_path = os.path.join(os.path.dirname(__file__), "offline.html")
    return FileResponse(offline_path)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "mqtt_connected": mqtt_bridge.client is not None,
        "active_connections": len(connection_manager.active_connections),
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/status")
async def system_status():
    """Get aggregated system status for dashboard"""
    return await get_system_status()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time chat"""
    await connection_manager.connect(websocket)

    # Send welcome message with system status
    try:
        status = await get_system_status()
        last_context = get_last_conversation_context()
        weather = await get_weather_brief()
        welcome_text = generate_welcome_text(
            mood=status.get("mood", {}),
            last_context=last_context,
            weather=weather
        )
        await websocket.send_json({
            "type": "welcome",
            "text": welcome_text,
            "services": status.get("services", {}),
            "stats": status.get("stats", {}),
            "mood": status.get("mood", {})
        })
    except Exception as e:
        logger.error(f"Error sending welcome message: {e}")

    try:
        while True:
            # Receive message from client
            data = await websocket.receive_json()
            message_type = data.get("type")

            if message_type == "message":
                # User sent a text message
                text = data.get("text", "").strip()
                if text:
                    # Add to history
                    user_message = ChatMessage(
                        role="user",
                        content=text,
                        timestamp=datetime.now().isoformat()
                    )
                    connection_manager.add_to_history(user_message)

                    # Broadcast to all clients
                    await connection_manager.broadcast({
                        "type": "message",
                        "message": user_message.model_dump()
                    })

                    # Forward to conversation service
                    try:
                        await mqtt_bridge.send_user_message(text)
                    except Exception as e:
                        logger.error(f"Failed to send message to MQTT: {e}")
                        await websocket.send_json({
                            "type": "error",
                            "message": "Failed to send message to conversation service"
                        })

            elif message_type == "voice":
                # User sent voice data
                audio_data = data.get("audio")
                format = data.get("format", "wav")

                try:
                    await mqtt_bridge.send_voice_data(audio_data, format)
                except Exception as e:
                    logger.error(f"Failed to send voice data: {e}")
                    await websocket.send_json({
                        "type": "error",
                        "message": "Failed to process voice input"
                    })

            elif message_type == "tts_toggle":
                # Toggle TTS output
                enabled = data.get("enabled", True)
                try:
                    await mqtt_bridge.toggle_tts(enabled)
                except Exception as e:
                    logger.error(f"Failed to toggle TTS: {e}")

            elif message_type == "diagnostic":
                # Avatar diagnostic report from browser
                diag_data = data.get("data", {})
                logger.info(f"=== AVATAR DIAGNOSTIC ===")
                for key, val in diag_data.items():
                    logger.info(f"  {key}: {val}")
                # Broadcast to all connections (for external monitoring)
                await connection_manager.broadcast({
                    "type": "diagnostic_response",
                    "data": diag_data
                })

            elif message_type == "diagnostic_response":
                # Forward diagnostic response from browser to all connections
                await connection_manager.broadcast({
                    "type": "diagnostic_response",
                    "data": data.get("data", {})
                })

            elif message_type == "request_diagnostic":
                # External request for diagnostics - ask all browsers
                await connection_manager.broadcast({
                    "type": "diagnostic_request"
                })

            elif message_type == "ping":
                # Keep-alive ping
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        connection_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        connection_manager.disconnect(websocket)


@app.post("/api/message")
async def send_message(text: str):
    """HTTP endpoint to send message (alternative to WebSocket)"""
    try:
        user_message = ChatMessage(
            role="user",
            content=text,
            timestamp=datetime.now().isoformat()
        )
        connection_manager.add_to_history(user_message)

        await connection_manager.broadcast({
            "type": "message",
            "message": user_message.model_dump()
        })

        await mqtt_bridge.send_user_message(text)

        return {"status": "sent", "message": user_message.model_dump()}

    except Exception as e:
        logger.error(f"Error sending message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history")
async def get_history():
    """Get message history"""
    return {
        "messages": [msg.model_dump() for msg in connection_manager.message_history]
    }


# ── Insights Page & API Proxies ──────────────────────────────────────────

@app.get("/manifest.json")
async def manifest():
    """Serve PWA manifest"""
    manifest_path = os.path.join(os.path.dirname(__file__), "manifest.json")
    return FileResponse(manifest_path, media_type="application/manifest+json")


@app.get("/insights", response_class=HTMLResponse)
async def insights_page():
    """Serve the insights/memory page"""
    insights_path = os.path.join(os.path.dirname(__file__), "insights.html")
    if os.path.exists(insights_path):
        return FileResponse(insights_path)
    return HTMLResponse("<h1>Insights page not found</h1>")


async def _proxy_memory_api(path: str, params: dict = None) -> dict:
    """Proxy a request to the memory service API."""
    import aiohttp
    url = f"http://localhost:8001{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {"error": f"Memory API returned {resp.status}"}
    except Exception as e:
        logger.error(f"Memory API proxy error: {e}")
        return {"error": str(e)}


@app.get("/api/insights/stats")
async def insights_stats():
    """Proxy to memory service /history/stats"""
    data = await _proxy_memory_api("/history/stats")
    # Transform top_tags from dict to array of objects for frontend
    if "top_tags" in data and isinstance(data["top_tags"], dict):
        data["top_tags"] = [{"tag": k, "count": v} for k, v in data["top_tags"].items()]
    return data


@app.get("/api/insights/recent")
async def insights_recent(page: int = 1, limit: int = 20):
    """Proxy to memory service /history/recent"""
    return await _proxy_memory_api("/history/recent", {"page": page, "limit": limit})


@app.get("/api/insights/search")
async def insights_search(q: str = "", limit: int = 20):
    """Proxy to memory service /history/search"""
    return await _proxy_memory_api("/history/search", {"q": q, "limit": limit})


@app.get("/api/insights/relationship")
async def insights_relationship():
    """Proxy to memory service /relationship"""
    return await _proxy_memory_api("/relationship")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=3001,
        reload=False,
        log_level="info"
    )
