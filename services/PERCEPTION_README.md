# Sentient Core Perception Layer

Complete world state aggregation service that unifies all sensor inputs into a coherent perception of the environment.

## Features

- **Multi-Sensor Fusion**: Aggregates vision, RF, audio, and system health data
- **Real-time Audio Monitoring**: Ambient noise classification using PyAudio
- **Time Awareness**: Time-of-day context and interaction tracking
- **Threat Detection**: Intelligent threat identification and severity scoring
- **Presence Detection**: Jack's location tracking via RF and vision
- **MQTT Publishing**: Unified world state published every 5 seconds

## World State Schema

```json
{
  "timestamp": "2026-01-29T10:30:45.123456",
  "jack_present": true,
  "jack_location": "living_room",
  "threat_level": 3,
  "active_threats": [
    {
      "source": "vision_camera_back",
      "type": "unknown_person",
      "severity": 7,
      "timestamp": "2026-01-29T10:30:40.123456",
      "location": "backyard",
      "details": {"confidence": 0.92}
    }
  ],
  "ambient_state": "quiet",
  "time_context": "morning",
  "last_interaction_seconds": 120,
  "system_health": {
    "orin-avatar": {
      "online": true,
      "cpu_percent": 45.2,
      "memory_percent": 62.8,
      "temperature": 58.5,
      "uptime": 86400
    }
  }
}
```

## Subscribed Topics

- `sentient/sensor/vision/+/detection` - Vision detections from all cameras
- `sentient/sensor/rf/detection` - RF device detections
- `sentient/system/status` - System health from all nodes

## Published Topics

- `sentient/world/state` - Unified world state (every 5 seconds)

## Installation

### Prerequisites

```bash
# Install Python dependencies
pip3 install aiomqtt paho-mqtt pyaudio

# Install PortAudio (required for PyAudio)
sudo apt-get install portaudio19-dev

# Ensure MQTT broker is running
sudo systemctl start mosquitto
sudo systemctl enable mosquitto
```

### Install as System Service

```bash
# Copy service file
sudo cp /opt/sentient-core/services/perception.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable and start service
sudo systemctl enable perception
sudo systemctl start perception

# Check status
sudo systemctl status perception
```

## Usage

### Manual Execution

```bash
# Basic usage (defaults to localhost:1883)
python3 /opt/sentient-core/services/perception.py

# Custom MQTT broker
python3 /opt/sentient-core/services/perception.py \
  --broker 192.168.1.100 \
  --port 1883 \
  --interval 5.0

# With authentication
python3 /opt/sentient-core/services/perception.py \
  --broker localhost \
  --port 1883 \
  --username sentient \
  --password secret \
  --interval 5.0
```

### Command-line Arguments

- `--broker` - MQTT broker hostname (default: localhost)
- `--port` - MQTT broker port (default: 1883)
- `--username` - MQTT username (optional)
- `--password` - MQTT password (optional)
- `--interval` - World state publish interval in seconds (default: 5.0)

## Testing

Run the comprehensive test suite:

```bash
# Make test script executable
chmod +x /opt/sentient-core/services/test_perception.py

# Run tests
python3 /opt/sentient-core/services/test_perception.py
```

The test suite will:
1. Verify MQTT connectivity
2. Send test vision detections
3. Send test RF detections
4. Send test system status
5. Validate world state structure and content
6. Test threat detection logic

## Monitoring

### View Logs

```bash
# Real-time logs
sudo journalctl -u perception -f

# Last 100 lines
sudo journalctl -u perception -n 100

# Logs since last boot
sudo journalctl -u perception -b
```

### Service Control

```bash
# Start service
sudo systemctl start perception

# Stop service
sudo systemctl stop perception

# Restart service
sudo systemctl restart perception

# Check status
sudo systemctl status perception

# Disable auto-start
sudo systemctl disable perception
```

## Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────────┐
│                  Perception Layer                       │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   Vision     │  │      RF      │  │   System     │  │
│  │  Detections  │  │  Detections  │  │   Status     │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                  │          │
│         └─────────┬───────┴──────────────────┘          │
│                   │                                      │
│         ┌─────────▼─────────┐                           │
│         │  State Aggregator │                           │
│         └─────────┬─────────┘                           │
│                   │                                      │
│    ┌──────────────┼──────────────┐                      │
│    │              │              │                      │
│ ┌──▼────┐  ┌─────▼──────┐  ┌───▼────┐                  │
│ │ Audio │  │    Time    │  │ Threat │                  │
│ │Monitor│  │  Awareness │  │Analyzer│                  │
│ └───────┘  └────────────┘  └────────┘                  │
│                   │                                      │
│         ┌─────────▼─────────┐                           │
│         │   World State     │                           │
│         │    Publisher      │                           │
│         └───────────────────┘                           │
│                                                          │
└─────────────────────────────────────────────────────────┘
                        │
                        ▼
            sentient/world/state (MQTT)
```

### Data Flow

1. **Sensor Input**: MQTT messages from vision, RF, and system monitors
2. **State Aggregation**: Combine all sensor data with timestamps
3. **Audio Monitoring**: Real-time ambient noise level classification
4. **Time Context**: Calculate time-of-day and interaction recency
5. **Threat Analysis**: Detect and score potential threats
6. **World State Generation**: Build unified state snapshot
7. **Publishing**: Broadcast to `sentient/world/state` every 5 seconds

## Threat Detection

### Threat Types

| Type | Source | Severity | Trigger |
|------|--------|----------|---------|
| `unknown_person` | Vision | 7 | Unknown face detected with >80% confidence |
| `suspicious_object` | Vision | 8 | Weapon/tool detected |
| `unknown_rf_device` | RF | 5 | Strong signal (-50 dBm) from unknown MAC |
| `rf_jamming` | RF | 9 | RF jamming detected |

### Threat Lifecycle

- Threats are active for 60 seconds
- Older threats automatically expire
- Threat level is weighted average of active threat severities
- Max threat level is 10

## Ambient State Classification

| State | Audio Level | Description |
|-------|-------------|-------------|
| `quiet` | 0-5% | Silence or minimal noise |
| `active` | 5-30% | Normal activity levels |
| `noisy` | 30-100% | High noise environment |

## Time Context

| Context | Time Range | Description |
|---------|------------|-------------|
| `morning` | 6am-12pm | Morning hours |
| `afternoon` | 12pm-6pm | Afternoon hours |
| `evening` | 6pm-10pm | Evening hours |
| `night` | 10pm-6am | Night hours |

## Presence Detection

Jack's presence is determined by:

1. **RF Detection**: Known device (phone, smartwatch) detected
2. **Vision Detection**: Face recognition or person ID match
3. **Interaction Recency**: Activity within last 5 minutes

Location is determined by:
- RF beacon location
- Camera coverage zone
- Last known position

## Troubleshooting

### Service won't start

```bash
# Check logs for errors
sudo journalctl -u perception -n 50

# Verify MQTT broker is running
sudo systemctl status mosquitto

# Test MQTT connection manually
mosquitto_sub -h localhost -t '#' -v
```

### Audio monitoring fails

```bash
# Check audio devices
python3 -c "import pyaudio; p = pyaudio.PyAudio(); print([p.get_device_info_by_index(i)['name'] for i in range(p.get_device_count())])"

# Test audio input
arecord -l

# Install PortAudio if missing
sudo apt-get install portaudio19-dev
pip3 install --upgrade pyaudio
```

### No world state messages

```bash
# Check if service is running
sudo systemctl status perception

# Monitor MQTT traffic
mosquitto_sub -h localhost -t 'sentient/world/state' -v

# Verify subscriptions
sudo journalctl -u perception -n 100 | grep "Subscribed"
```

## Performance

- **CPU Usage**: ~10-20% on Jetson Orin (single core)
- **Memory**: ~100-200 MB
- **Audio Latency**: <50ms
- **State Update Rate**: 5 seconds (configurable)
- **MQTT QoS**: Level 1 (at least once delivery)

## Development

### Adding New Sensors

1. Add subscription in `run()` method
2. Create handler method (e.g., `handle_new_sensor()`)
3. Update `WorldState` dataclass if needed
4. Update aggregation logic in `build_world_state()`

### Modifying Threat Detection

Edit the `_analyze_vision_threats()` or `_analyze_rf_threats()` methods to add custom threat detection logic.

### Changing Publish Rate

```bash
# Faster updates (2 seconds)
python3 perception.py --interval 2.0

# Slower updates (10 seconds)
python3 perception.py --interval 10.0
```

## License

Part of Sentient Core - Autonomous AI System
