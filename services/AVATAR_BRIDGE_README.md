# Avatar Bridge Service

Production-ready bridge connecting Cortana's cognitive system to avatar visualization at port 9001.

## Overview

The Avatar Bridge translates high-level cognitive states (emotions, thinking, speaking) into real-time avatar animations and expressions via MQTT messaging.

## Features

### Emotion System
- **10 distinct emotion states** with unique colors and expressions
- Smooth intensity control (0.0 - 1.0)
- Emotion-driven color palette
- Expression mapping to avatar visualization

### Supported Emotions
| Emotion | Color | Expression | Use Case |
|---------|-------|------------|----------|
| Neutral | Blue (100,150,255) | neutral | Default idle state |
| Happy | Warm Yellow (255,200,100) | smile | Positive interactions |
| Amused | Orange (255,180,120) | grin | Playful responses |
| Concerned | Purple (150,100,200) | worried | Problem detection |
| Focused | Cyan (100,200,255) | focused | Active processing |
| Curious | Green (150,255,150) | interested | Learning mode |
| Protective | Red (255,100,100) | alert | Security/warnings |
| Affectionate | Pink (255,150,200) | warm | Personal connection |
| Thoughtful | Deep Blue (120,120,200) | contemplative | Deep thinking |
| Alert | Bright Yellow (255,220,100) | attentive | Wake word detected |

### Lifelike Animations

#### Idle Behaviors
- **Breathing animation**: 4-second sine wave cycle with subtle pulsing
- **Blinking**: Random intervals between 2-6 seconds
- **Attention drift**: Subtle gaze wandering when idle for >5 seconds
- **Micro-movements**: Realistic small adjustments for organic feel

#### Reactive Behaviors
- **Wake word detection**: Immediate snap to attention with alert emotion
- **Speaking state**: Focused forward gaze with increased attention
- **Thinking state**: Unfocused gaze with slight drift
- **Eye contact simulation**: Returns to center periodically

### Attention/Gaze Control
- **X axis**: -1.0 (left) to 1.0 (right)
- **Y axis**: -1.0 (down) to 1.0 (up)
- **Focus**: 0.0 (unfocused) to 1.0 (focused)
- Smooth transitions between states
- Context-aware positioning (speaking vs thinking vs idle)

## MQTT Topics

### Subscribed (Input from Cognitive System)
```
sentient/conversation/response   - Detect speaking state
sentient/emotion/state           - Emotion updates from contemplation
sentient/conversation/thinking   - Thinking/deliberation state
sentient/tts/synthesize          - TTS events for lip sync timing
sentient/wake/detected           - Wake word detection events
```

### Published (Output to Avatar)
```
sentient/persona/emotion         - Emotion, color, expression updates
sentient/persona/speaking        - Speaking state (boolean)
sentient/persona/attention       - Gaze direction (x, y, focus)
sentient/audio/tts/phonemes      - Phoneme data for lip sync
sentient/persona/idle            - Breathing/idle animation data
```

## Message Formats

### Emotion Update (Published)
```json
{
  "emotion": "happy",
  "expression": "smile",
  "color": {
    "r": 255,
    "g": 200,
    "b": 100
  },
  "intensity": 0.7,
  "timestamp": 1706542800.123
}
```

### Speaking State (Published)
```json
{
  "speaking": true,
  "timestamp": 1706542800.123
}
```

### Attention Vector (Published)
```json
{
  "x": 0.0,
  "y": 0.1,
  "focus": 0.9,
  "timestamp": 1706542800.123
}
```

### Idle Animation (Published)
```json
{
  "phase": 0.75,
  "intensity": 0.1,
  "timestamp": 1706542800.123
}
```

## Installation

### Systemd Service
```bash
# Install service
sudo cp /opt/sentient-core/systemd/sentient-avatar-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sentient-avatar-bridge
sudo systemctl start sentient-avatar-bridge
```

### Manual Run
```bash
cd /opt/sentient-core
python3 services/avatar_bridge.py
```

### Check Status
```bash
sudo systemctl status sentient-avatar-bridge
journalctl -u sentient-avatar-bridge -f
```

## Dependencies

```bash
pip install aiomqtt
```

## Configuration

Edit `/opt/sentient-core/config/cortana.toml`:
```toml
[mqtt]
broker = "localhost"
port = 1883
```

## Architecture

```
┌─────────────────────────────────────────┐
│     Cognitive System (Redis/MQTT)       │
│  - Conversation Service                 │
│  - Contemplation Engine                 │
│  - Memory System                        │
│  - TTS Service                          │
│  - Wake Word Detector                   │
└────────────┬────────────────────────────┘
             │ MQTT Messages
             │ (emotion, speaking, thinking)
             ▼
┌─────────────────────────────────────────┐
│        Avatar Bridge Service            │
│  - Emotion state translation            │
│  - Speaking detection                   │
│  - Attention/gaze control               │
│  - Idle animation generation            │
│  - Breathing/blinking simulation        │
└────────────┬────────────────────────────┘
             │ MQTT Messages
             │ (persona/*, audio/*)
             ▼
┌─────────────────────────────────────────┐
│   Avatar Visualization (Port 9001)      │
│  - 3D rendering                         │
│  - Expression morphing                  │
│  - Color transitions                    │
│  - Lip sync animation                   │
│  - Particle effects                     │
└─────────────────────────────────────────┘
```

## Behavior Flow

### Wake Word Detected
```
Wake Word → ALERT emotion + snap to center (0,0) with max focus
```

### Conversation Started
```
User speaks → CURIOUS/FOCUSED emotion
Cortana thinks → THOUGHTFUL with unfocused drift
Cortana responds → HAPPY + speaking=true + forward gaze
TTS ends → speaking=false
```

### Idle State (no activity for 5+ seconds)
```
Breathing cycle (4s sine wave)
Random blinking (2-6s intervals)
Attention drift (subtle random movements)
Return to center every ~15s
```

## Timing Constants

```python
IDLE_ANIMATION_INTERVAL = 3.0      # Idle loop update rate
ATTENTION_UPDATE_INTERVAL = 0.5    # Gaze update rate
BREATHING_CYCLE_DURATION = 4.0     # One full breath
BLINK_MIN_INTERVAL = 2.0           # Fastest blink rate
BLINK_MAX_INTERVAL = 6.0           # Slowest blink rate
```

## Error Handling

- **MQTT disconnection**: Automatic reconnection with exponential backoff
- **Malformed messages**: Graceful fallback to neutral state
- **Missing data**: Sensible defaults for all fields
- **Service crash**: Systemd auto-restart with 5s delay

## Logging

Logs written to:
- `/var/log/sentient/avatar_bridge.log` (file)
- `journalctl -u sentient-avatar-bridge` (systemd)

Log levels:
- **INFO**: State changes, emotion updates, major events
- **DEBUG**: Detailed animation frames, attention drift
- **ERROR**: MQTT failures, parsing errors, exceptions

## Testing

```bash
# Basic functionality test
cd /opt/sentient-core
python3 -c "
from services.avatar_bridge import AvatarBridge, EmotionState
import asyncio

async def test():
    bridge = AvatarBridge()
    print(f'Emotion states: {[e.value for e in EmotionState]}')
    print(f'Default emotion: {bridge.state.emotion}')

asyncio.run(test())
"
```

## Integration with Avatar Visualization

The avatar visualization (running on port 9001) should subscribe to:
- `sentient/persona/emotion` - Update visual appearance
- `sentient/persona/speaking` - Activate lip sync
- `sentient/persona/attention` - Control gaze/head orientation
- `sentient/audio/tts/phonemes` - Fine-grained lip sync
- `sentient/persona/idle` - Breathing/pulse effects

## Future Enhancements

- [ ] Phoneme-level lip sync from TTS service
- [ ] Head nodding during conversation
- [ ] Hand gesture coordination
- [ ] Environmental awareness (camera input)
- [ ] Multi-avatar synchronization
- [ ] Emotion learning from user feedback
- [ ] Advanced procedural animations

## Troubleshooting

### Avatar not responding
1. Check MQTT broker: `sudo systemctl status mosquitto`
2. Check bridge service: `sudo systemctl status sentient-avatar-bridge`
3. Monitor MQTT traffic: `mosquitto_sub -t 'sentient/persona/#' -v`

### Emotions not updating
1. Check cognitive services are running
2. Verify emotion messages: `mosquitto_sub -t 'sentient/emotion/state' -v`
3. Check bridge logs: `journalctl -u sentient-avatar-bridge -n 50`

### Speaking state stuck
1. Verify TTS service is publishing completion events
2. Check for timeout in bridge logs
3. Fallback auto-stop after 3 seconds should activate

## Performance

- **CPU**: < 1% idle, < 3% during animation
- **Memory**: ~50MB resident
- **Latency**: < 50ms emotion update to avatar
- **MQTT QoS**: 0 for frequent updates (attention), 1 for state changes (emotion, speaking)

## License

Part of Sentient Core - Cortana AI Assistant
