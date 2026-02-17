# Avatar Bridge Quick Start Guide

## Installation

1. **Install the service**
```bash
sudo systemctl daemon-reload
sudo systemctl enable sentient-avatar-bridge
sudo systemctl start sentient-avatar-bridge
```

2. **Verify it's running**
```bash
sudo systemctl status sentient-avatar-bridge
```

Expected output:
```
● sentient-avatar-bridge.service - Sentient Core - Avatar Bridge Service
   Loaded: loaded (/etc/systemd/system/sentient-avatar-bridge.service)
   Active: active (running)
```

## Testing

### Manual Test
```bash
cd /opt/sentient-core
python3 services/test_avatar_bridge.py
```

This will:
- Connect to MQTT
- Send test emotion states
- Simulate conversation events
- Monitor avatar responses

### Quick MQTT Test
```bash
# Terminal 1: Monitor avatar output
mosquitto_sub -t 'sentient/persona/#' -v

# Terminal 2: Send test emotion
mosquitto_pub -t 'sentient/emotion/state' -m '{"emotion":"happy","intensity":0.8}'
```

## What You Should See

### On Wake Word Detection
```
[Avatar]: Emotion → ALERT (bright yellow)
[Avatar]: Attention → (0.0, 0.0) with max focus
[Avatar]: Expression → attentive
```

### During Conversation
```
[Avatar]: Emotion → HAPPY (warm yellow)
[Avatar]: Speaking → true
[Avatar]: Attention → forward gaze, focused
```

### While Thinking
```
[Avatar]: Emotion → THOUGHTFUL (deep blue)
[Avatar]: Attention → slight drift, unfocused
```

### Idle State
```
[Avatar]: Breathing pulse (4s cycle)
[Avatar]: Random blinking (2-6s intervals)
[Avatar]: Subtle attention drift
```

## Troubleshooting

### Service won't start
```bash
# Check logs
journalctl -u sentient-avatar-bridge -n 50

# Common issues:
# - MQTT broker not running: sudo systemctl start mosquitto
# - Python dependencies: pip install aiomqtt
```

### No avatar updates
```bash
# Verify MQTT topics
mosquitto_sub -t 'sentient/#' -v

# Check bridge is publishing
mosquitto_sub -t 'sentient/persona/emotion' -v
```

### Avatar stuck in one state
```bash
# Restart the bridge
sudo systemctl restart sentient-avatar-bridge

# Send manual reset
mosquitto_pub -t 'sentient/emotion/state' -m '{"emotion":"neutral","intensity":0.5}'
```

## Integration Checklist

- [ ] MQTT broker running (mosquitto)
- [ ] Avatar bridge service active
- [ ] Avatar visualization subscribing to `sentient/persona/*`
- [ ] Cognitive services publishing to input topics
- [ ] Test scenarios passing

## Next Steps

1. **Integrate with conversation service**
   - Verify `sentient/conversation/response` publishes on speech
   - Verify `sentient/emotion/state` updates from contemplation

2. **Connect TTS service**
   - Publish `event: "start"` when TTS begins
   - Publish `event: "end"` when TTS completes
   - Optional: Send phoneme data for lip sync

3. **Configure avatar visualization**
   - Subscribe to all `sentient/persona/*` topics
   - Map emotion colors to visual appearance
   - Implement expression morphing
   - Add lip sync animation on `speaking: true`

4. **Test end-to-end**
   - Say wake word → avatar snaps to attention
   - Ask question → avatar shows curiosity/focus
   - Cortana responds → avatar speaks with happy emotion
   - Idle timeout → avatar breathes and drifts naturally

## Performance Tuning

Edit timing constants in `avatar_bridge.py`:
```python
IDLE_ANIMATION_INTERVAL = 3.0      # Lower = more frequent updates
BREATHING_CYCLE_DURATION = 4.0     # Adjust breath speed
BLINK_MIN_INTERVAL = 2.0           # Faster blinking
BLINK_MAX_INTERVAL = 6.0           # Slower blinking
```

Restart service after changes:
```bash
sudo systemctl restart sentient-avatar-bridge
```

## Support

- **Logs**: `journalctl -u sentient-avatar-bridge -f`
- **Documentation**: `/opt/sentient-core/services/AVATAR_BRIDGE_README.md`
- **Test script**: `python3 services/test_avatar_bridge.py`
