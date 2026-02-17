# Sentient-Core v2 Rebuild Plan

## Current State (v1 Audit Results)

### Code Locations (Fragmented)
- `/opt/sentient-core/` - documented services (memory, conversation, perception, contemplation, proactive, avatar-bridge, web-chat, voice, wake-word)
- `/home/cortana/sentient-core/` - persona main.py, orchestrator, control-center, TTS, STT, vision, RF, camera, avatar renderer
- `/home/cortana/cortana/backend/` - avatar backend (separate venv)
- `/opt/sentient/` - device agent (runs as ROOT)

### Active Production Bugs
1. Proactive service: wrong contemplation URL (`:5001` should be `:8002`)
2. Proactive service: wrong ntfy URL (`localhost:8082`)
3. Contemplation: hardcodes `llama3.2:1b` but system runs `qwen3:4b`
4. Conversation: chat input subscription commented out (hidden cortana dependency)
5. Camera service: crash-looping
6. Vestigial services: memory standalone + contemplation standalone waste ~200MB RAM

### Tech Stack
- Python 3.10+ (AsyncIO), FastAPI, MQTT (Mosquitto), Redis, Ollama (qwen3:4b)
- sentence-transformers, Piper TTS, faster-whisper STT, OpenWakeWord
- Three.js avatar, NVIDIA Jetson Orin Nano (ARM64, CUDA)

## v2 Target Architecture

### Directory Structure
```
/opt/sentient-core/
  sentient/                     # Python package root
    __init__.py
    config/
      __init__.py
      loader.py                 # Reads cortana.toml, env vars, validates
      models.py                 # Typed config dataclasses
    common/
      __init__.py
      service_base.py           # SentientService base class
      mqtt_topics.py            # Canonical topic constants
      logging.py                # Structured JSON logging
    personality/
      cortana_core.txt          # Keep as-is
    services/
      memory/engine.py + api.py
      contemplation/engine.py + api.py
      perception/engine.py + api.py
      conversation/orchestrator.py
      proactive/engine.py
      notifications/engine.py
      voice/pipeline.py, wake_word.py, tts.py, stt.py
      vision/camera.py, integration.py, rf_detection.py
      avatar/bridge.py, renderer.py
    interfaces/
      web_chat/                 # Keep as-is
      cli/
  tests/
  systemd/
  pyproject.toml
  Makefile
```

### Key Principles
1. Single config source (cortana.toml + env vars)
2. SentientService base class (MQTT, health, shutdown, logging)
3. One service = one process (no vestigial services)
4. Standardized MQTT topics
5. Fast contemplation by default (30-45s, not 200s)
6. Secrets in environment, not source code

### Phases
| Phase | What |
|-------|------|
| 0: Stabilize | Fix bugs, disable crash-looping, remove vestigial |
| 1: Config | Central config loader + cortana.toml |
| 2: Structure | Package structure + service base class |
| 3: Migrate | Move all services to new structure |
| 4: Comms | Unified MQTT topics, standardize aiomqtt |
| 5: Harden | API auth, secrets, CORS, logging |
| 6: Test | pytest suite, integration tests, benchmarks |

### Services to KEEP
- 3-tier memory, perception, personality definition, MQTT architecture
- Web chat + avatar, avatar bridge, notifications, voice pipeline, systemd

### Services to REBUILD
- Config management, code organization, proactive engine
- Contemplation performance, MQTT topics, camera service, security

### Services to ADD
- Central config loader, service base class, health dashboard
- Response streaming, test suite, structured logging, graceful degradation
- Rate limiting, monitoring/metrics, single deploy script
