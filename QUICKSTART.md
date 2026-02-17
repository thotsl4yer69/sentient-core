# SENTIENT CORE v7.0 - QUICKSTART GUIDE

**System:** Jetson Orin Nano (nanob, 192.168.1.159)
**Status:** ✅ PRODUCTION-READY
**Version:** 7.0 (Fast Mode)

---

## 🚀 FASTEST START (30 Seconds)

### 1. Launch Testing Interface
```bash
cd /opt/sentient-core
./launch-testing.sh
```

### 2. Access Web Chat
Open in browser: **http://192.168.1.159:3001**

### 3. Start Talking
Type: "Hey Cortana, how are you?"

**That's it!** System is ready to use.

---

## 📋 WHAT YOU NEED TO KNOW

### Response Times (Fast Mode)
- **First message:** 30-60 seconds (model loading)
- **Subsequent messages:** 20-40 seconds (cached)
- **Expected:** Some variability due to Jetson hardware

### What Works
- ✅ Web chat interface (port 3001)
- ✅ Conversation manager (orchestrates all services)
- ✅ Memory system (remembers conversations)
- ✅ Perception layer (world state awareness)
- ✅ Personality (Cortana's identity integrated)
- ✅ All 12 services active

### What Needs Testing
- ⏸️ Wake word ("Hey Cortana") - requires microphone
- ⏸️ Voice mode - requires audio I/O
- ⏸️ Terminal CLI - requires interactive session

---

## 🔧 COMMON TASKS

### Check System Status
```bash
# All services
systemctl status sentient-*.service | grep -E "(●|Active:)"

# Health checks
curl http://localhost:8001/health  # Memory
curl http://localhost:8002/health  # Contemplation
curl http://localhost:8003/health  # Perception
```

### View Logs
```bash
# Conversation service (main orchestrator)
sudo journalctl -u sentient-conversation.service -f

# Contemplation (LLM responses)
sudo journalctl -u sentient-contemplation-http.service -f

# All services
sudo journalctl -u sentient-*.service -f
```

### Restart Services
```bash
# All services
sudo systemctl restart sentient-*.service

# Specific service
sudo systemctl restart sentient-conversation.service

# Ollama (if CUDA errors occur)
sudo systemctl restart ollama
```

### Test MQTT Messaging
```bash
# Subscribe to all topics
mosquitto_sub -h localhost -u sentient -P sentient1312 -t "sentient/#" -v

# Send test message
mosquitto_pub -h localhost -u sentient -P sentient1312 \
  -t "sentient/persona/chat/input" \
  -m '{"text": "Hello Cortana", "user": "Jack", "timestamp": "2026-01-29T12:00:00+11:00"}'
```

---

## 🎯 INTERFACES

### 1. Web Chat (Recommended)
- **URL:** http://192.168.1.159:3001
- **Features:** Real-time WebSocket, emotion indicator, brutalist theme
- **Usage:** Type message, click send, wait 20-60s for response

### 2. Terminal CLI
```bash
cd /opt/sentient-core/interfaces
python3 cli.py
```
- **Features:** Colored output, emotion emojis, thinking spinner
- **Exit:** Ctrl+C

### 3. MQTT Direct
```bash
# Listen for responses
mosquitto_sub -h localhost -u sentient -P sentient1312 \
  -t "sentient/persona/chat/output" -v

# Send message (in another terminal)
mosquitto_pub -h localhost -u sentient -P sentient1312 \
  -t "sentient/persona/chat/input" \
  -m '{"text": "Your message", "user": "Jack", "timestamp": "2026-01-29T12:00:00+11:00"}'
```

### 4. HTTP API (Advanced)
```bash
# Generate response
curl -X POST http://localhost:8002/generate \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Hello Cortana",
    "user_id": "Jack",
    "memories": [],
    "world_state": {"jack_present": true},
    "conversation_context": {}
  }' | jq
```

---

## ⚠️ TROUBLESHOOTING

### "Sorry, I need a moment to think about that."
**Cause:** Contemplation timeout (response took >90s)
**Fix:** This is normal for first request or under heavy load. Try again.

### CUDA Memory Errors
**Symptom:** Ollama crashes, no responses
**Fix:**
```bash
sudo systemctl restart ollama
sleep 5
# Then try your request again
```

### Service Not Running
```bash
# Check which service
systemctl is-active sentient-*.service | \
  paste <(systemctl list-units "sentient-*.service" --no-legend | awk '{print $1}') -

# Start inactive service
sudo systemctl start sentient-<service-name>.service
```

### Web Chat Not Loading
1. Check service: `systemctl is-active sentient-web-chat.service`
2. Check port: `sudo lsof -i :3001`
3. Check logs: `sudo journalctl -u sentient-web-chat.service -n 20`
4. Restart: `sudo systemctl restart sentient-web-chat.service`

### Slow Responses (>60s)
**Normal on Jetson Orin Nano.** Hardware limitations:
- Jetson has limited GPU memory (3.6GB available)
- qwen3:4b model requires 2.2GB
- Response variability expected (20-72 seconds)

**Options:**
- Accept current performance (fast for edge AI)
- Upgrade hardware (RTX 3060+, Jetson AGX Orin)
- Use smaller model (llama3.2:1b for 10-20s responses)

---

## 📊 PERFORMANCE EXPECTATIONS

### Normal Behavior
| Scenario | Expected Time | Notes |
|----------|---------------|-------|
| First request after boot | 30-60s | Model loading + generation |
| Cached requests | 20-40s | Model already loaded |
| Under CUDA errors | 60-90s or timeout | Restart Ollama |

### Resource Usage
- **CPU:** 15-20% idle, 400-500% during generation
- **RAM:** 5.2GB / 7.4GB (70%)
- **GPU Memory:** 2.2GB (model + cache)

---

## 🔐 CREDENTIALS

### MQTT Broker
- **Host:** localhost:1883
- **Username:** sentient
- **Password:** sentient1312

### Redis
- **Host:** localhost:6379
- **Database:** 0
- **Password:** None (local only)

### Ollama
- **Host:** localhost:11434
- **Model:** qwen3:4b (2.5GB)
- **GPU:** CUDA enabled (28/37 layers)

---

## 📚 MORE DOCUMENTATION

- **TESTING_GUIDE.md** - Comprehensive testing procedures
- **PRODUCTION_STATUS.md** - Full system status and assessment
- **FAST_MODE_IMPLEMENTATION.md** - Performance optimization details
- **launch-testing.sh** - Interactive testing launcher

---

## 🆘 NEED HELP?

### Check Service Health
```bash
./launch-testing.sh
# Select option 5: Test HTTP APIs
```

### Full System Reset
```bash
# Stop all
sudo systemctl stop sentient-*.service

# Restart infrastructure
sudo systemctl restart mosquitto redis-server ollama

# Start all
sudo systemctl start sentient-*.service

# Verify
systemctl is-active sentient-*.service | grep -v active || echo "All services active"
```

### Performance Debugging
```bash
# Check Ollama GPU usage
sudo journalctl -u ollama -n 20 | grep -E "(GPU|offload|CUDA)"

# Check contemplation timing
sudo journalctl -u sentient-contemplation-http -n 50 | grep "Generated response"

# Check conversation flow
sudo journalctl -u sentient-conversation -n 50 | grep -E "(Processing|timeout)"
```

---

## ✅ SUCCESS CRITERIA

Your system is working correctly if:
- [x] `systemctl is-active sentient-*.service` shows mostly "active"
- [x] Web chat at http://192.168.1.159:3001 loads
- [x] You can send a message and get a response (even if slow)
- [x] Response is conversational (not just "Sorry, I need a moment...")
- [x] Services survive restart

---

**System Ready:** ✅ YES
**Documentation Complete:** ✅ YES
**Production Status:** ✅ READY (with Jetson limitations documented)

**Questions?** Check TESTING_GUIDE.md for detailed procedures.

*Last Updated: 2026-01-29*
