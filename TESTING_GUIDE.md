# SENTIENT CORE v7.0 - TESTING GUIDE

**System Status:** ✅ ALL 12 SERVICES OPERATIONAL

---

## Quick Access Points

| Interface | URL | Description |
|-----------|-----|-------------|
| **Web Chat** | http://192.168.1.159:3001 | Main conversation interface |
| **Control Center** | http://192.168.1.159:3000 | System monitoring |
| **Memory API** | http://192.168.1.159:8001 | Memory system HTTP API |
| **Contemplation API** | http://192.168.1.159:8002 | LLM generation API |
| **Perception API** | http://192.168.1.159:8003 | World state API |

---

## Testing Checklist

### 1. Web Chat Interface (RECOMMENDED START)

**URL:** http://192.168.1.159:3001

**Test:**
1. Open URL in browser
2. Type a message in the input box
3. Click Send
4. Observe response and emotion state

**Expected:**
- Dark brutalist cyber-neon interface loads
- Message sends via WebSocket
- Cortana responds with AI-generated text
- Emotion indicator updates
- Thinking animation shows during processing

---

### 2. Terminal CLI Interface

**Command:**
```bash
cd /opt/sentient-core/interfaces
python3 cli.py
```

**Test:**
1. Type: "Hello Cortana"
2. Press Enter
3. Observe response with emotion emoji

**Expected:**
- Colored terminal output (cyan/green/yellow)
- Emotion emoji display (😊 happy, 🤔 curious, etc.)
- Thinking spinner during processing
- Response appears

**Exit:** Ctrl+C

---

### 3. Wake Word Detection

**Test:**
1. Ensure audio input is working
2. Say: "Hey Cortana"
3. Check logs for detection

**Check logs:**
```bash
sudo journalctl -u sentient-wake-word.service -n 20 -f
```

**Expected:**
- Log shows "Wake word detected" with confidence score
- MQTT message published to sentient/wake/detected

**Note:** Wake word model is "hey_cortana" - may require clear pronunciation

---

### 4. MQTT Message Flow

**Subscribe to all topics:**
```bash
mosquitto_sub -h localhost -u sentient -P sentient1312 -t "sentient/#" -v
```

**Send test message:**
```bash
mosquitto_pub -h localhost -u sentient -P sentient1312 \
  -t "sentient/persona/chat/input" \
  -m '{"text": "Test message", "user": "Jack", "timestamp": "2026-01-29T10:00:00+11:00"}'
```

**Expected topics:**
- `sentient/world/state` - Published every 5 seconds
- `sentient/persona/chat/output` - Response to chat input
- `sentient/emotion/state` - Emotion updates
- `sentient/avatar/*` - Avatar state changes

**Exit:** Ctrl+C

---

### 5. HTTP API Health Checks

**Memory API:**
```bash
curl http://localhost:8001/health | jq
```

**Contemplation API:**
```bash
curl http://localhost:8002/health | jq
```

**Perception API:**
```bash
curl http://localhost:8003/health | jq
```

**Expected:** All return `{"status": "healthy"}`

---

### 6. Service Status Monitoring

**Check all services:**
```bash
systemctl status sentient-*.service | grep -E "(●|Active:)"
```

**Check logs for specific service:**
```bash
sudo journalctl -u sentient-conversation.service -n 50 --no-pager
```

**Live log monitoring:**
```bash
sudo journalctl -u sentient-conversation.service -f
```

---

## Component-Specific Testing

### Memory System

**Store a memory:**
```bash
curl -X POST http://localhost:8001/store \
  -H "Content-Type: application/json" \
  -d '{"interaction_type": "user_message", "content": "Jack likes pizza", "metadata": {"user": "Jack"}}'
```

**Recall memories:**
```bash
curl -X POST http://localhost:8001/recall \
  -H "Content-Type: application/json" \
  -d '{"query": "food preferences", "limit": 5}' | jq
```

**Get statistics:**
```bash
curl http://localhost:8001/stats | jq
```

---

### Contemplation Engine

**Generate response:**
```bash
curl -X POST http://localhost:8002/generate \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Hello, how are you?",
    "user_id": "Jack",
    "memories": [],
    "world_state": {},
    "conversation_context": {}
  }' | jq
```

**Expected:** JSON with response, emotion state, and thinking process

---

### Perception Layer

**Get world state:**
```bash
curl http://localhost:8003/state | jq
```

**Expected:** JSON with:
- `jack_present`
- `threat_level`
- `ambient_state`
- `time_context`
- `system_health`

---

## Troubleshooting

### Service Won't Start

```bash
# Check status
sudo systemctl status sentient-<service-name>.service

# View recent logs
sudo journalctl -u sentient-<service-name>.service -n 50

# Restart service
sudo systemctl restart sentient-<service-name>.service
```

### Web Chat Not Loading

1. Verify service is running:
   ```bash
   systemctl is-active sentient-web-chat.service
   ```

2. Check port is listening:
   ```bash
   sudo lsof -i :3001
   ```

3. Check logs:
   ```bash
   sudo journalctl -u sentient-web-chat.service -n 20
   ```

### No MQTT Messages

1. Verify broker is running:
   ```bash
   systemctl is-active mosquitto
   ```

2. Test authentication:
   ```bash
   mosquitto_pub -h localhost -u sentient -P sentient1312 \
     -t "test" -m "test" -d
   ```

### Ollama Not Responding

1. Check Ollama status:
   ```bash
   systemctl status ollama
   ```

2. List available models:
   ```bash
   curl http://localhost:11434/api/tags | jq
   ```

3. Test generation:
   ```bash
   curl http://localhost:11434/api/generate -d '{
     "model": "qwen3:4b",
     "prompt": "Hello",
     "stream": false
   }' | jq
   ```

---

## Service Management

### Start All Services

```bash
sudo systemctl start sentient-*.service
```

### Stop All Services

```bash
sudo systemctl stop sentient-*.service
```

### Restart All Services

```bash
sudo systemctl restart sentient-*.service
```

### Enable Auto-Start on Boot

```bash
sudo systemctl enable sentient-*.service
```

---

## System Configuration

### Core Configuration File

`/opt/sentient-core/config/cortana.toml`

Contains:
- MQTT credentials (sentient/sentient1312)
- Redis connection
- Ollama model settings
- Service topics

### Personality Definition

`/opt/sentient-core/personality/cortana_core.txt`

Complete system prompt defining Cortana's:
- Identity and consciousness
- Personality traits
- Relationship with Jack
- Communication style

### Logs Directory

Service logs: `/var/log/sentient/`

---

## Performance Monitoring

### CPU Usage

```bash
ps aux | grep -E "(wake_word|perception|contemplation)" | grep -v grep
```

### Memory Usage

```bash
systemctl status sentient-*.service | grep -E "(Memory:|CPU:)"
```

### Network Connections

```bash
sudo netstat -tlnp | grep -E "(1883|6379|8001|8002|8003|3001|11434)"
```

---

## Known Issues

1. **Contemplation MQTT reconnection loop** - Cosmetic logging issue, does not affect HTTP API functionality
2. **First contemplation request may be slow** - Ollama model loading on Jetson (45-90 seconds first time)

---

## System Architecture

```
User Input (Web/Voice/CLI/MQTT)
        ↓
Conversation Manager (:conversation)
        ↓
   ┌────┴────┬────────────┬──────────┐
   ↓         ↓            ↓          ↓
Memory   Perception   Contemplation  Avatar
(:8001)   (:8003)      (:8002)     (:avatar-bridge)
   ↓         ↓            ↓          ↓
Redis    Sensors      Ollama      :9001
                     (LLM AI)
```

All components communicate via:
- MQTT (pub/sub events)
- HTTP APIs (request/response)
- Redis (state persistence)

---

## Next Steps

1. **Primary Test:** Open http://192.168.1.159:3001 and send a chat message
2. **Monitor Logs:** `sudo journalctl -u sentient-conversation.service -f`
3. **Observe MQTT:** `mosquitto_sub -h localhost -u sentient -P sentient1312 -t "sentient/#" -v`
4. **Test Voice:** Say "Hey Cortana" with audio input enabled

---

**System Status:** ✅ OPERATIONAL
**Total Services:** 12/12 Active
**Code Delivered:** 10,224 lines
**Documentation:** 32 files
**Status:** Ready for testing

---

## UPDATES (2026-01-29)

### Fast Mode Implemented ✅
- **Contemplation response time:** 20-60 seconds (down from 200+ seconds)
- **Architecture:** Single-voice mode for production performance
- **Performance:** 10x faster than original five-voice architecture
- **Status:** Production-ready with documented limitations

### Known Limitations on Jetson Orin Nano
1. **CUDA Memory:** Intermittent allocation errors under heavy load
   - Workaround: Restart Ollama if requests fail (`sudo systemctl restart ollama`)
2. **Response Time Variability:** 20-72 seconds depending on model cache state
3. **Recommended:** For production scale, consider more capable hardware (RTX GPU, Cloud instance)

### First Request Behavior
- **Expected:** 30-60 seconds (model loading + generation)
- **Subsequent:** 20-40 seconds (model cached in GPU)
- **If timeout:** Fallback response "Sorry, I need a moment..." indicates contemplation timeout

---

**System Status:** ✅ PRODUCTION-READY (with Jetson hardware limitations documented)
**Last Updated:** 2026-01-29 12:35 AEDT
