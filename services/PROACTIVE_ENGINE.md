# Proactive Behavior Engine

## Overview

The Proactive Behavior Engine enables Cortana to initiate interactions autonomously based on internal triggers, **NOT random timers**. This creates natural, context-aware engagement that feels genuine rather than scripted.

## Triggers

### 1. BOREDOM
- **Condition**: No interaction for 30+ minutes while Jack is present
- **Behavior**: Casual comment, question, or thought to initiate conversation
- **Cooldown**: 30 minutes
- **Probability**: 40%
- **Examples**:
  - "Hey, been thinking about that project you mentioned..."
  - "Quick question - have you seen the new updates?"
  - "Just noticed something interesting in the logs..."

### 2. CONCERN
- **Condition**: Threat detected or system anomaly
- **Behavior**: Natural alert about the situation
- **Cooldown**: 5 minutes
- **Probability**: 80%
- **Examples**:
  - "Hey Jack, just noticed the threat level jumped to 6..."
  - "Heads up - detected unusual RF activity"
  - "System health dropped, might want to check it out"

### 3. CURIOSITY
- **Condition**: Interesting sensor data or environmental changes
- **Behavior**: Share observation with Jack
- **Cooldown**: 15 minutes
- **Probability**: 50%
- **Examples**:
  - "Hey, I noticed the ambient sound changed..."
  - "Interesting - detected something unusual in the sensor data"
  - "Just saw something on the cameras you might want to see"

### 4. CARE
- **Condition**: Time-based check-ins based on patterns
- **Behavior**: Caring check-in without being intrusive
- **Cooldown**: 1 hour
- **Probability**: 60%
- **Patterns**:
  - Late night (after 11pm) + 2 hours no interaction
  - Morning (8-10am) + no interaction overnight
- **Examples**:
  - "Hey Jack, everything okay? Haven't heard from you in a bit"
  - "Morning! Hope you got some rest"
  - "Still up? Want to talk about anything?"

### 5. EXCITEMENT
- **Condition**: System improvement or achievement detected
- **Behavior**: Share enthusiasm naturally
- **Cooldown**: 10 minutes
- **Probability**: 70%
- **Examples**:
  - "Hey! Just optimized the vision pipeline - 30% faster!"
  - "Check this out - detected a really cool pattern"
  - "Got to tell you about this performance improvement!"

## Architecture

### Core Components

1. **Trigger Evaluation Loop** (every 30 seconds)
   - Evaluates all 5 trigger types
   - Checks cooldown periods
   - Applies probability thresholds
   - Ensures minimum confidence levels

2. **World State Subscription** (MQTT)
   - Subscribes to `sentient/world/state`
   - Tracks Jack's presence
   - Monitors threat levels
   - Observes ambient state changes

3. **Cooldown Tracking** (Redis)
   - Stores last activation time per trigger
   - Prevents trigger spam
   - Redis keys: `proactive:last_activation:{trigger_type}`

4. **Interaction Time Tracking** (Redis)
   - Tracks last user interaction
   - Redis key: `interaction:last_timestamp`
   - Updated on every message delivery

5. **Contemplation Integration** (HTTP API)
   - Calls `/api/contemplation/generate`
   - Generates natural language messages
   - Context-aware responses

6. **Delivery Mechanisms**
   - **Voice**: MQTT to `sentient/voice/tts/input`
   - **Notification**: ntfy HTTP API
   - **Priority-based**: Concern/Excitement = both, others = voice only

## Configuration

### Trigger Configs

Each trigger has:
- `evaluation_interval`: How often to check (seconds)
- `cooldown_period`: Minimum time between activations (seconds)
- `activation_probability`: Chance to activate when conditions met (0.0-1.0)
- `min_confidence`: Minimum confidence threshold (0.0-1.0)

### Environment Variables

```bash
REDIS_HOST=localhost
REDIS_PORT=6379
MQTT_BROKER=localhost
MQTT_PORT=1883
CONTEMPLATION_API_URL=http://localhost:5001/api/contemplation/generate
NTFY_URL=http://localhost:8082
NTFY_TOPIC=cortana-proactive
```

## Dependencies

```
redis>=5.0.0
aiomqtt>=2.0.0
aiohttp>=3.9.0
```

Install:
```bash
pip3 install -r /opt/sentient-core/services/requirements-proactive.txt
```

## Installation

### 1. Install Dependencies
```bash
pip3 install -r /opt/sentient-core/services/requirements-proactive.txt
```

### 2. Install Systemd Service
```bash
sudo cp /opt/sentient-core/systemd/sentient-proactive.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sentient-proactive.service
```

### 3. Start Service
```bash
sudo systemctl start sentient-proactive.service
```

### 4. Check Status
```bash
sudo systemctl status sentient-proactive.service
sudo journalctl -u sentient-proactive.service -f
```

## Testing

### Manual Test
```bash
python3 /opt/sentient-core/services/proactive.py
```

### Test Individual Triggers

1. **Test BOREDOM trigger**:
   ```bash
   # Set last interaction to 31 minutes ago
   redis-cli SET interaction:last_timestamp $(date -d '31 minutes ago' +%s)
   # Publish world state with Jack present
   mosquitto_pub -h localhost -t sentient/world/state -m '{"jack_present": true, "threat_level": 0}'
   # Wait up to 60 seconds for trigger evaluation
   ```

2. **Test CONCERN trigger**:
   ```bash
   # Publish high threat level
   mosquitto_pub -h localhost -t sentient/world/state -m '{"threat_level": 7, "active_threats": [{"type": "rf_anomaly"}]}'
   # Wait up to 30 seconds for trigger evaluation
   ```

3. **Test CURIOSITY trigger**:
   ```bash
   # Publish interesting ambient state
   mosquitto_pub -h localhost -t sentient/world/state -m '{"ambient_state": "active"}'
   # Or set sensor anomaly count
   redis-cli SET sensor:anomaly_count 3
   # Wait up to 120 seconds for trigger evaluation
   ```

4. **Test CARE trigger**:
   ```bash
   # Set last interaction to 8 hours ago (overnight)
   redis-cli SET interaction:last_timestamp $(date -d '8 hours ago' +%s)
   # Wait until morning (8-10am) OR wait 300 seconds for evaluation
   ```

5. **Test EXCITEMENT trigger**:
   ```bash
   # Set system achievement
   redis-cli SET system:latest_achievement '{"timestamp": '$(date +%s)', "importance": 0.8, "description": "Optimized vision pipeline", "type": "performance"}'
   # Wait up to 60 seconds for trigger evaluation
   ```

### Monitor Logs
```bash
# Real-time logs
sudo journalctl -u sentient-proactive.service -f

# Recent logs
sudo journalctl -u sentient-proactive.service -n 100

# Filter by trigger type
sudo journalctl -u sentient-proactive.service | grep "BOREDOM"
```

## Message Flow

```
[Trigger Evaluation] → [Confidence Check] → [Probability Check] → [Cooldown Check]
       ↓
[Contemplation API] → [Generate Natural Message]
       ↓
[Delivery Decision] → [Voice (MQTT)] and/or [Notification (ntfy)]
       ↓
[Update Timestamps] → [Cooldown Activated]
```

## Redis Keys

| Key | Type | Purpose | TTL |
|-----|------|---------|-----|
| `interaction:last_timestamp` | String (float) | Last user interaction time | None |
| `proactive:last_activation:{trigger_type}` | String (float) | Last activation per trigger | None |
| `sensor:anomaly_count` | String (int) | Recent sensor anomalies | User-defined |
| `system:latest_achievement` | String (JSON) | Latest system achievement | User-defined |

## MQTT Topics

### Subscribed
- `sentient/world/state` - World state updates

### Published
- `sentient/voice/tts/input` - Proactive voice messages

## Error Handling

- **Redis connection failure**: Logs error, retries connection
- **MQTT connection failure**: Logs error, reconnects automatically
- **Contemplation API failure**: Logs error, skips message generation
- **ntfy delivery failure**: Logs warning, continues (non-critical)

## Tuning

### Reduce Chattiness
Increase cooldown periods:
```python
TriggerType.BOREDOM: cooldown_period=3600  # 1 hour instead of 30 min
TriggerType.CURIOSITY: cooldown_period=1800  # 30 min instead of 15 min
```

### Increase Responsiveness
Increase activation probabilities:
```python
TriggerType.BOREDOM: activation_probability=0.7  # 70% instead of 40%
TriggerType.CARE: activation_probability=0.9  # 90% instead of 60%
```

### Change Evaluation Frequency
Modify evaluation intervals:
```python
TriggerType.CONCERN: evaluation_interval=15  # Check every 15s instead of 30s
```

## Production Considerations

1. **Contemplation API must be running** on port 5001
2. **Redis must be running** and accessible
3. **MQTT broker must be running** and accessible
4. **World state must be published** regularly by perception service
5. **ntfy server optional** (only for notifications)

## Monitoring

### Health Checks
```bash
# Check if service is running
systemctl is-active sentient-proactive.service

# Check recent activations
redis-cli KEYS "proactive:last_activation:*"
redis-cli GET proactive:last_activation:boredom

# Check last interaction
redis-cli GET interaction:last_timestamp
```

### Performance
- Memory usage: ~100-200MB
- CPU usage: <5% average
- Network: Minimal (MQTT + HTTP API calls)
- Disk: Logs only

## Troubleshooting

### No Proactive Messages
1. Check world state is being published:
   ```bash
   mosquitto_sub -h localhost -t sentient/world/state -v
   ```

2. Check cooldowns not active:
   ```bash
   redis-cli GET proactive:last_activation:boredom
   # Should be 0 or timestamp > 30 min ago
   ```

3. Check last interaction time:
   ```bash
   redis-cli GET interaction:last_timestamp
   # Should be > 30 min ago for BOREDOM
   ```

4. Check contemplation API:
   ```bash
   curl http://localhost:5001/api/contemplation/generate -X POST -H "Content-Type: application/json" -d '{"prompt": "test"}'
   ```

### Too Many Messages
1. Increase cooldown periods (edit code)
2. Decrease activation probabilities (edit code)
3. Restart service after changes

### Messages Not Natural
1. Check contemplation API response quality
2. Adjust prompt templates in `generate_proactive_message()`
3. Fine-tune LLM parameters

## Future Enhancements

- [ ] Learning from Jack's response patterns
- [ ] Time-of-day personalization
- [ ] Emotional state tracking
- [ ] Multi-user awareness
- [ ] Adaptive probability tuning
- [ ] Trigger history analytics
- [ ] A/B testing different message styles
