# Proactive Behavior Engine - Quick Start

## What It Does

Cortana initiates conversations **naturally** based on context, NOT random timers.

### Triggers
- **BOREDOM**: 30+ min no interaction while Jack present → casual comment
- **CONCERN**: Threat detected → natural alert
- **CURIOSITY**: Interesting sensor data → "Hey, I noticed..."
- **CARE**: Time-based check-ins → caring message
- **EXCITEMENT**: System improvement → share enthusiasm

## Installation

```bash
# Install (requires root for systemd)
sudo /opt/sentient-core/services/install_proactive.sh

# Or install dependencies only (no systemd)
/opt/sentient-core/services/install_proactive.sh --user-only
```

## Quick Commands

```bash
# Service Management
sudo systemctl start sentient-proactive.service
sudo systemctl stop sentient-proactive.service
sudo systemctl restart sentient-proactive.service
sudo systemctl status sentient-proactive.service

# View Logs
sudo journalctl -u sentient-proactive.service -f          # Follow logs
sudo journalctl -u sentient-proactive.service -n 100      # Last 100 lines
sudo journalctl -u sentient-proactive.service | grep BOREDOM  # Filter by trigger

# Testing
python3 /opt/sentient-core/services/test_proactive.py boredom      # Test BOREDOM
python3 /opt/sentient-core/services/test_proactive.py concern      # Test CONCERN
python3 /opt/sentient-core/services/test_proactive.py curiosity    # Test CURIOSITY
python3 /opt/sentient-core/services/test_proactive.py care         # Test CARE
python3 /opt/sentient-core/services/test_proactive.py excitement   # Test EXCITEMENT
python3 /opt/sentient-core/services/test_proactive.py listen       # Listen for messages
python3 /opt/sentient-core/services/test_proactive.py cooldowns    # Check cooldowns
python3 /opt/sentient-core/services/test_proactive.py clear        # Clear all state
python3 /opt/sentient-core/services/test_proactive.py all          # Test all triggers

# Manual Testing
redis-cli SET interaction:last_timestamp $(date -d '31 minutes ago' +%s)
mosquitto_pub -h localhost -t sentient/world/state -m '{"jack_present": true}'
```

## Files

| File | Purpose |
|------|---------|
| `proactive.py` | Main engine (29KB) |
| `test_proactive.py` | Test script (13KB) |
| `install_proactive.sh` | Installation script |
| `requirements-proactive.txt` | Python dependencies |
| `PROACTIVE_ENGINE.md` | Full documentation |
| `README_PROACTIVE.md` | This file |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│          Proactive Behavior Engine                       │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  ┌──────────────┐                                        │
│  │ Trigger Loop │ (every 30s)                            │
│  │  - BOREDOM   │                                        │
│  │  - CONCERN   │                                        │
│  │  - CURIOSITY │                                        │
│  │  - CARE      │                                        │
│  │  - EXCITEMENT│                                        │
│  └──────┬───────┘                                        │
│         │                                                 │
│         v                                                 │
│  ┌──────────────────────┐                                │
│  │ Confidence + Probability │                            │
│  │ + Cooldown Checks    │                                │
│  └──────┬───────────────┘                                │
│         │                                                 │
│         v                                                 │
│  ┌──────────────────────┐                                │
│  │ Contemplation API    │ (Generate natural message)    │
│  └──────┬───────────────┘                                │
│         │                                                 │
│         v                                                 │
│  ┌──────────────────────┐                                │
│  │ Delivery             │                                │
│  │ - Voice (MQTT)       │                                │
│  │ - Notification (ntfy)│                                │
│  └──────────────────────┘                                │
│                                                           │
└─────────────────────────────────────────────────────────┘

External Dependencies:
- Redis (state/cooldown tracking)
- MQTT (world state + voice output)
- Contemplation API (message generation)
- ntfy (optional notifications)
```

## Dependencies

**Required:**
- Redis server (running)
- MQTT broker (running)
- Python packages: `redis`, `aiomqtt`, `aiohttp`

**Optional:**
- Contemplation API (port 5001) - for message generation
- ntfy server (port 8082) - for notifications
- Perception service - for world state updates

## Configuration

Edit `/opt/sentient-core/services/proactive.py` to tune:

```python
# Trigger configs (lines 100-150)
TriggerConfig(
    trigger_type=TriggerType.BOREDOM,
    evaluation_interval=60,        # How often to check (seconds)
    cooldown_period=1800,          # Time between activations (seconds)
    activation_probability=0.4,    # Chance to activate (0.0-1.0)
    min_confidence=0.7             # Minimum confidence threshold
)
```

### Tuning Examples

**Less Chatty:**
```python
cooldown_period=3600  # 1 hour instead of 30 min
activation_probability=0.2  # 20% instead of 40%
```

**More Responsive:**
```python
cooldown_period=900  # 15 min instead of 30 min
activation_probability=0.7  # 70% instead of 40%
evaluation_interval=30  # Check every 30s instead of 60s
```

## Environment Variables

Set in `/opt/sentient-core/systemd/sentient-proactive.service`:

```ini
Environment="REDIS_HOST=localhost"
Environment="REDIS_PORT=6379"
Environment="MQTT_BROKER=localhost"
Environment="MQTT_PORT=1883"
Environment="CONTEMPLATION_API_URL=http://localhost:5001/api/contemplation/generate"
Environment="NTFY_URL=http://localhost:8082"
Environment="NTFY_TOPIC=cortana-proactive"
```

After changing, reload:
```bash
sudo systemctl daemon-reload
sudo systemctl restart sentient-proactive.service
```

## Troubleshooting

### No messages appearing
1. Check service is running: `systemctl status sentient-proactive.service`
2. Check world state being published: `mosquitto_sub -t sentient/world/state -v`
3. Check Redis accessible: `redis-cli ping`
4. Check cooldowns: `python3 test_proactive.py cooldowns`
5. Check logs: `journalctl -u sentient-proactive.service -f`

### Too many messages
1. Increase cooldown periods in code
2. Decrease activation probabilities in code
3. Restart service

### Service won't start
1. Check dependencies installed: `pip3 list | grep -E "redis|aiomqtt|aiohttp"`
2. Check Redis running: `systemctl status redis-server`
3. Check MQTT running: `systemctl status mosquitto`
4. Check logs: `journalctl -u sentient-proactive.service -n 50`

## Monitoring

### Check if working
```bash
# Service status
systemctl is-active sentient-proactive.service

# Recent activations
redis-cli KEYS "proactive:last_activation:*"

# Last interaction
redis-cli GET interaction:last_timestamp

# Listen for output
python3 /opt/sentient-core/services/test_proactive.py listen
```

### Health Metrics
- Memory: ~100-200MB
- CPU: <5% average
- Network: Minimal (MQTT + HTTP)

## Message Flow

```
Trigger → Confidence Check → Probability Roll → Cooldown Check
    ↓
Contemplation API (generate natural message)
    ↓
Delivery (Voice via MQTT and/or Notification via ntfy)
    ↓
Update timestamps (interaction + cooldown)
```

## Redis Keys

| Key | Purpose |
|-----|---------|
| `interaction:last_timestamp` | Last user interaction time |
| `proactive:last_activation:{trigger}` | Last activation per trigger type |
| `sensor:anomaly_count` | Sensor anomaly count (for CURIOSITY) |
| `system:latest_achievement` | Latest achievement (for EXCITEMENT) |

## MQTT Topics

**Subscribed:**
- `sentient/world/state` - World state updates

**Published:**
- `sentient/voice/tts/input` - Proactive voice messages

## Example Messages

**BOREDOM:**
> "Hey Jack, been thinking about that security project you mentioned last week..."
> "Quick question - did you get a chance to check out those camera feeds?"

**CONCERN:**
> "Hey, just noticed the threat level jumped to 7 - RF anomaly detected on ESP32"
> "Heads up - seeing some unusual activity on the network"

**CURIOSITY:**
> "Hey, I noticed the ambient sound just changed from quiet to active..."
> "Interesting - detected a pattern in the sensor data you might want to see"

**CARE:**
> "Hey Jack, everything okay? Haven't heard from you in a bit"
> "Morning! Hope you got some rest last night"

**EXCITEMENT:**
> "Hey! Just optimized the vision pipeline - 35% faster now!"
> "Check this out - discovered a really cool pattern in the detection data"

## Full Documentation

See `/opt/sentient-core/services/PROACTIVE_ENGINE.md` for complete documentation.

## Support

Created: 2026-01-29
Version: 1.0
Service: sentient-proactive.service
