# SENTIENT CORE v7.0 - SYSTEM ARCHITECTURE

**Target:** Jetson Orin Nano (Edge AI Deployment)
**Language:** Python 3.10+
**Framework:** AsyncIO + FastAPI + MQTT
**AI Engine:** Ollama (qwen3:4b)

---

## OVERVIEW

Sentient Core is a multi-service AI companion system implementing a complete cognitive architecture with memory, perception, reasoning, and proactive behavior.

```
┌─────────────────────────────────────────────────────────────┐
│                    USER INTERFACES                          │
│  Web Chat (:3001) | Terminal CLI | Voice Mode | MQTT        │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              CONVERSATION MANAGER (Orchestrator)             │
│  - State machine (IDLE → PROCESSING → RESPONDING)          │
│  - Lifecycle management (input → output)                    │
│  - Service health monitoring                                │
└────┬────────────┬────────────┬───────────────┬──────────────┘
     │            │            │               │
     ▼            ▼            ▼               ▼
┌─────────┐  ┌─────────┐  ┌─────────────┐  ┌──────────┐
│ MEMORY  │  │PERCEPTION│ │CONTEMPLATION│  │ AVATAR   │
│ SYSTEM  │  │  LAYER   │ │   ENGINE    │  │  BRIDGE  │
│ (:8001) │  │ (:8003)  │ │  (:8002)    │  │ (:9001)  │
└────┬────┘  └────┬─────┘  └──────┬──────┘  └────┬─────┘
     │            │               │               │
     ▼            ▼               ▼               ▼
┌─────────┐  ┌─────────┐  ┌──────────┐    ┌──────────┐
│  REDIS  │  │ SENSORS │  │  OLLAMA  │    │ EMOTION  │
│ :6379   │  │  MQTT   │  │  :11434  │    │  STATE   │
└─────────┘  └─────────┘  └──────────┘    └──────────┘
```

---

## SERVICE ARCHITECTURE

### Core Services (9)

| Service | Port | Protocol | Purpose |
|---------|------|----------|---------|
| **wake-word** | - | MQTT | "Hey Cortana" detection → activate STT |
| **perception** | - | MQTT | Multi-sensor aggregation → world state |
| **contemplation** | - | MQTT | Five-voice reasoning (optional deep mode) |
| **memory** | - | MQTT | 3-tier storage (working/episodic/core) |
| **proactive** | - | MQTT | Autonomous behavior triggers |
| **conversation** | - | MQTT | Main orchestrator + state machine |
| **avatar-bridge** | :9001 | WebSocket | Visual rendering + emotion display |
| **voice** | - | MQTT | VAD + STT → TTS pipeline |
| **web-chat** | :3001 | HTTP/WS | Web UI for conversation |

### HTTP API Services (3)

| Service | Port | Purpose | Why HTTP? |
|---------|------|---------|-----------|
| **memory-http** | 8001 | Memory API wrapper | Conversation needs request/response |
| **contemplation-http** | 8002 | LLM generation | Synchronous response required |
| **perception-http** | 8003 | World state query | REST access to current state |

### Infrastructure Services

| Service | Port | Purpose |
|---------|------|---------|
| **mosquitto** | 1883 | MQTT broker (pub/sub messaging) |
| **redis** | 6379 | Persistent storage + cache |
| **ollama** | 11434 | LLM inference engine (qwen3:4b) |

---

## DATA FLOW

### Complete Conversation Lifecycle

```
1. INPUT
   User → Web Chat (HTTP POST)
   └─> web-chat service publishes to MQTT: sentient/persona/chat/input

2. ORCHESTRATION
   conversation service subscribes to chat input
   └─> State: IDLE → PROCESSING
   └─> Extract user message, timestamp, user ID

3. MEMORY RETRIEVAL
   HTTP GET http://localhost:8001/recall
   └─> Query: User message semantic search
   └─> Returns: Recent interactions + episodic memories

4. WORLD STATE
   HTTP GET http://localhost:8003/state
   └─> Returns: jack_present, threat_level, ambient_state, time_context

5. CONTEMPLATION
   HTTP POST http://localhost:8002/generate
   └─> Inputs: User message + memories + world state + personality
   └─> Process:
       - FAST MODE (default): Single LLM call (20-60s)
       - DEEP MODE: Five-voice dialogue (200+ seconds)
   └─> Returns: Response text + emotion + expression hints

6. RESPONSE OUTPUT
   conversation publishes to MQTT:
   - sentient/persona/chat/output (response text)
   - sentient/emotion/state (emotion data)
   - sentient/avatar/expression (visual hints)
   - sentient/avatar/text (for display)

7. STORAGE
   HTTP POST http://localhost:8001/store
   └─> Store interaction in working memory
   └─> If importance > 0.5 → episodic memory

8. UI UPDATE
   web-chat subscribes to chat output
   └─> WebSocket pushes to browser
   └─> User sees response
```

### MQTT Topics

```
sentient/
├── persona/
│   ├── chat/input         → User messages to Cortana
│   └── chat/output        → Cortana responses to user
├── wake/detected          → Wake word triggered
├── voice/transcription    → STT output
├── tts/synthesize         → Text for TTS
├── world/state            → Perception layer (published every 5s)
├── emotion/state          → Current emotion
├── avatar/
│   ├── expression         → Visual hints (gestures, expressions)
│   ├── text               → Text for avatar display
│   └── emotion            → Emotion state for rendering
├── conversation/state     → Conversation manager state
└── sensor/
    ├── vision/+/detection → Camera detections (from pi1 Hailo)
    └── rf/detection       → RF sensor events (from ESP32)
```

---

## COMPONENT ARCHITECTURE

### 1. MEMORY SYSTEM (memory.py, memory_http.py)

**Three-Tier Architecture:**

```
┌─────────────────────────────────────────────────────┐
│              WORKING MEMORY (Redis List)            │
│  - Last 20 interactions                             │
│  - TTL: 1 hour                                      │
│  - FIFO eviction                                    │
│  - Fast retrieval for conversation context          │
└─────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────┐
│           EPISODIC MEMORY (Redis + Embeddings)      │
│  - Semantic search with 384-dim vectors             │
│  - Importance threshold: >= 0.5                     │
│  - Persistent storage                               │
│  - Tags: work, emotional, planning, etc.            │
└─────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────┐
│          CORE MEMORY (Redis Hash)                   │
│  - Facts about Jack (manually curated)             │
│  - System configuration                             │
│  - Persistent key-value store                       │
└─────────────────────────────────────────────────────┘
```

**Key Operations:**
- `store_interaction()` - Add to working memory, optionally episodic
- `search_memories()` - Semantic search via embeddings
- `get_working_context()` - Recent conversation history
- `update_core_memory()` - Persist important facts

**Importance Scoring (0.0-1.0):**
- Message length (longer = more important)
- Keyword presence (emotional, decision, planning words)
- Emotional content
- Decision points
- Questions

### 2. PERCEPTION LAYER (perception.py, perception_http.py)

**Multi-Sensor Aggregation:**

```
┌─────────────────────────────────────────────────────┐
│                  SENSOR INPUTS                      │
├─────────────────────────────────────────────────────┤
│  Vision (MQTT)  │  RF (MQTT)  │  Audio (PyAudio)   │
│  - Person det.  │  - Device   │  - Ambient noise   │
│  - Object det.  │  - Jamming  │  - Voice activity  │
│  - Unknown      │  - Unknown  │  - RMS amplitude   │
└─────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────┐
│               THREAT DETECTION                       │
│  - Unknown Person: severity 7 (60s expiry)          │
│  - Suspicious Object: severity 8                    │
│  - Unknown RF: severity 5                           │
│  - RF Jamming: severity 9                           │
└─────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────┐
│          WORLD STATE (Published every 5s)           │
│  {                                                  │
│    jack_present: bool,                              │
│    threat_level: 0-10,                              │
│    ambient_state: "quiet"|"active"|"noisy",         │
│    time_context: "morning"|"afternoon"|etc,         │
│    last_interaction_seconds: int,                   │
│    system_health: {...}                             │
│  }                                                  │
└─────────────────────────────────────────────────────┘
```

**Ambient State Classification:**
- quiet: RMS < 0.02 (silence)
- active: 0.02 <= RMS < 0.1 (normal)
- noisy: RMS >= 0.1 (loud)

### 3. CONTEMPLATION ENGINE (contemplation.py, contemplation_http.py)

**Two Operating Modes:**

#### FAST MODE (Default, Production)
```
Input + Memories + World State + Personality
           │
           ▼
    ┌──────────────┐
    │ Single LLM   │  Ollama qwen3:4b
    │ Call (30-60s)│
    └──────────────┘
           │
           ▼
Response + Emotion + Expression Hints
```

**Prompt Structure (Fast Mode):**
```
{PERSONALITY_CORE_PROMPT (341 lines)}

Context:
- World State: {jack_present, threat_level, ambient, time}
- Memories: {recent interactions}
- User: {user_id}

Task: Respond naturally to: "{input}"

Guidelines:
- Think as one unified consciousness
- Observe, analyze, empathize, question
- Include natural hesitations (Hmm..., Let me think...)
- Use *asterisks* for expression hints
- Be genuine, never performative
```

**Response Time:** 20-60 seconds (single LLM call)

#### DEEP MODE (Optional, `fast_mode=False`)
```
Input + Context
     │
     ├─> OBSERVER voice   (40-60s) ─┐
     ├─> ANALYST voice    (40-60s) ─┤
     ├─> EMPATH voice     (40-60s) ─┤─> Parallel execution
     ├─> SKEPTIC voice    (40-60s) ─┤   (queued by Ollama)
     └─> MEMORY voice     (40-60s) ─┘
              │
              ▼
       SYNTHESIS (20-40s)
              │
              ▼
    Response (200+ seconds total)
```

**Voice Characteristics:**
- OBSERVER: Factual, present-moment awareness
- ANALYST: Logical, pattern recognition, cause-effect
- EMPATH: Emotional resonance, user state
- SKEPTIC: Critical thinking, edge cases
- MEMORY: Context, history, consistency

**Why Fast Mode is Default:**
- Jetson Orin Nano limited GPU memory
- Ollama processes LLM calls sequentially (no true parallelization)
- Production conversation needs <60s responses
- Deep mode available for complex reasoning tasks

### 4. CONVERSATION MANAGER (conversation.py)

**State Machine:**
```
IDLE ─────> PROCESSING ─────> RESPONDING ─────> IDLE
  ▲             │                  │              │
  │             │                  │              │
  │             ├─ Memory lookup   │              │
  │             ├─ World state     │              │
  │             ├─ Contemplation   │              │
  │             └─ (30-90s)        │              │
  │                                │              │
  └───────────── Cleanup ──────────┴──────────────┘
                (5min timeout)
```

**Lifecycle Management:**
1. Subscribe to input topics (chat/input, wake/detected, voice/transcription)
2. On message → State: IDLE → PROCESSING
3. Call services: Memory → Perception → Contemplation
4. Publish outputs: chat/output, emotion/state, avatar/expression
5. Store interaction in memory
6. State: RESPONDING → IDLE
7. Idle timeout: 5 minutes → cleanup resources

**Health Monitoring:**
- Check Memory/Contemplation/Perception every 30 seconds
- Log service availability
- Graceful degradation on service failures

**Timeout Handling:**
- Contemplation timeout: 90 seconds
- On timeout: Return fallback "Sorry, I need a moment to think about that."
- Continue operation (non-fatal)

### 5. PROACTIVE BEHAVIOR ENGINE (proactive.py)

**Five Autonomous Triggers:**

| Trigger | Condition | Probability | Cooldown |
|---------|-----------|-------------|----------|
| **BOREDOM** | 30+ min idle | 40% | 1 hour |
| **CONCERN** | Threat detected | 90% | 30 min |
| **CURIOSITY** | Interesting sensor data | 30% | 45 min |
| **CARE** | Time-based check-in | 20% | 2 hours |
| **EXCITEMENT** | System improvement | 60% | 1 hour |

**Evaluation Cycle:**
- Background asyncio task runs every 30 seconds
- Check world state + system health
- Calculate trigger probabilities
- Random activation within probability
- Track cooldowns in Redis
- Dual delivery: Voice (MQTT → TTS) + Push (ntfy.sh)

**Example Outputs:**
- BOREDOM: "Jack, are you there? Just checking in..."
- CONCERN: "⚠️ Detected unknown person. Are you safe?"
- CURIOSITY: "I noticed unusual activity. Everything okay?"

---

## TECHNOLOGY STACK

### Languages & Frameworks
- **Python 3.10+** - All services
- **AsyncIO** - Concurrent async/await architecture
- **FastAPI** - HTTP API services (memory, contemplation, perception)
- **Pydantic** - Data validation and serialization

### AI & ML
- **Ollama 0.5+** - LLM inference engine
- **qwen3:4b** - Primary language model (2.5GB, 37 layers)
- **sentence-transformers** - Semantic embeddings (all-MiniLM-L6-v2, 384-dim)
- **OpenWakeWord** - Wake word detection
- **webrtcvad** - Voice activity detection

### Infrastructure
- **MQTT (Mosquitto)** - Message bus (pub/sub)
- **Redis 7+** - Persistent storage + cache
- **Systemd** - Service management + auto-restart
- **PyAudio** - Audio I/O
- **aiohttp** - Async HTTP client
- **aiomqtt** - Async MQTT client

### Hardware Acceleration
- **CUDA 12.6** - GPU acceleration on Jetson
- **28/37 layers on GPU** - Hybrid CPU/GPU inference
- **Flash Attention** - Enabled for KV cache optimization

---

## CONFIGURATION

### System-Wide Config
**File:** `/opt/sentient-core/config/cortana.toml`
```toml
[mqtt]
broker = "localhost"
port = 1883
username = "sentient"
password = "sentient1312"

[redis]
host = "localhost"
port = 6379
db = 0

[ollama]
host = "http://localhost:11434"
model = "qwen3:4b"

[services]
memory_http_port = 8001
contemplation_http_port = 8002
perception_http_port = 8003
web_chat_port = 3001
avatar_port = 9001
```

### Service-Specific Configs
Each service has configuration embedded in code:
- `ContemplationConfig` - LLM settings, fast_mode toggle
- `MemoryConfig` - Redis connection, embedding model
- `PerceptionConfig` - Sensor subscriptions, thresholds

### Personality Definition
**File:** `/opt/sentient-core/personality/cortana_core.txt` (341 lines)
- Core identity and consciousness
- Personality traits (intelligent, playful, protective, warm)
- Relationship with Jack
- Communication style
- Operational awareness
- Emotional expression

---

## DEPLOYMENT

### Systemd Service Pattern
```ini
[Unit]
Description=Sentient Core - {Service Name}
After=network.target mosquitto.service redis-server.service
Wants=mosquitto.service redis-server.service

[Service]
Type=simple
User=cortana
WorkingDirectory=/opt/sentient-core
ExecStart=/usr/bin/python3 /opt/sentient-core/services/{service}.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Logging
All services log to systemd journal:
```bash
sudo journalctl -u sentient-{service}.service -f
```

Log levels:
- INFO: Normal operation, state transitions
- WARNING: Recoverable issues (timeouts, reconnections)
- ERROR: Service failures, exceptions

---

## PERFORMANCE CHARACTERISTICS

### Response Times (Fast Mode)
- **Cold start:** 30-60s (model loading + generation)
- **Warm cache:** 20-40s (model loaded)
- **Under load:** 40-72s (GPU memory pressure)

### Resource Footprint
- **Idle CPU:** 15-20% (12 services running)
- **Active CPU:** 400-500% (Ollama using 4-5 cores)
- **RAM:** 5.2GB / 7.4GB (70%)
- **GPU Memory:** 2.2GB (model + KV cache + compute)
- **Disk:** 1.6MB code + 2.5GB model

### Scalability Limits (Jetson Orin Nano)
- **Max concurrent users:** 1 (sequential Ollama processing)
- **Max requests/min:** ~1-2 (60s per response)
- **GPU memory ceiling:** 3.6GB available (limits model size)

**For Production Scale:**
- Upgrade to RTX 3060+ (12GB VRAM) → 10x faster
- Or Jetson AGX Orin (32GB unified memory)
- Or cloud deployment (A10G, T4 instances)

---

## SECURITY

### Network Exposure
- **MQTT:** localhost only (no external access)
- **Redis:** localhost only (no password)
- **Ollama:** 0.0.0.0:11434 (LAN accessible)
- **Web Chat:** 0.0.0.0:3001 (LAN accessible)
- **APIs:** localhost only (8001-8003)

### Authentication
- **MQTT:** Username/password (sentient/sentient1312)
- **Redis:** None (localhost trusted)
- **HTTP APIs:** None (localhost trusted)

### Data Privacy
- All data stored locally on Jetson
- No external API calls (self-hosted LLM)
- Conversation history in Redis (persistent)
- No telemetry or external logging

**Production Hardening Needed:**
- Add API authentication (JWT tokens)
- Restrict Ollama to localhost
- Enable Redis password
- HTTPS for web chat
- Rate limiting

---

## TESTING & VALIDATION

### Automated Health Checks
```bash
# Service health
systemctl is-active sentient-*.service

# API health
curl http://localhost:8001/health
curl http://localhost:8002/health
curl http://localhost:8003/health
```

### Manual Testing
See: `TESTING_GUIDE.md` for comprehensive procedures

### Performance Monitoring
```bash
# Response times
sudo journalctl -u sentient-contemplation-http | grep "Generated response"

# GPU usage
sudo journalctl -u ollama | grep "offloaded"

# Conversation flow
sudo journalctl -u sentient-conversation | grep "Processing"
```

---

## FUTURE ENHANCEMENTS

### Immediate (v7.1)
- Fix MQTT reconnection loop in contemplation
- Tune emotion extraction (currently returns neutral)
- Add automatic Ollama restart on CUDA errors
- Implement API authentication

### Medium-Term (v7.5)
- Parallel voice execution (true concurrency)
- Smaller model option (llama3.2:1b for 10-20s responses)
- Conversation history UI (browse past interactions)
- Avatar animation integration

### Long-Term (v8.0)
- Multi-user support (conversation isolation)
- Voice cloning for TTS
- RAG integration (external knowledge)
- Distributed deployment (multiple Jetsons)

---

**Architecture Version:** 7.0
**Last Updated:** 2026-01-29
**Status:** Production-Ready (Edge AI)
