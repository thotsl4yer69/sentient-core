# Perception Layer - Quick Start Guide

## 60-Second Setup

```bash
# 1. Install
cd /opt/sentient-core/services
./install_perception.sh

# 2. Test
python3 test_perception.py

# 3. Deploy
sudo ./install_perception.sh --install-service
sudo systemctl start perception

# 4. Monitor
sudo journalctl -u perception -f
```

## What It Does

Aggregates all sensor data into unified world state every 5 seconds:

```
Vision + RF + Audio + Time → World State → MQTT
```

## World State Output

```json
{
  "timestamp": "2026-01-29T10:30:45.123456",
  "jack_present": true,
  "jack_location": "living_room",
  "threat_level": 3,
  "active_threats": [...],
  "ambient_state": "quiet",
  "time_context": "morning",
  "last_interaction_seconds": 120,
  "system_health": {...}
}
```

Published to: `sentient/world/state`

## Service Commands

```bash
# Start
sudo systemctl start perception

# Stop
sudo systemctl stop perception

# Status
sudo systemctl status perception

# Logs
sudo journalctl -u perception -f

# Restart
sudo systemctl restart perception
```

## Manual Run

```bash
# Basic
python3 perception.py

# Custom broker
python3 perception.py --broker 192.168.1.100 --port 1883

# Faster updates (2 seconds)
python3 perception.py --interval 2.0
```

## Monitor Output

```bash
# Watch world state
mosquitto_sub -h localhost -t 'sentient/world/state' -v

# Watch all sensor input
mosquitto_sub -h localhost -t 'sentient/sensor/#' -v

# Watch system status
mosquitto_sub -h localhost -t 'sentient/system/status' -v
```

## Troubleshooting

### Service won't start
```bash
sudo journalctl -u perception -n 50
sudo systemctl status mosquitto
```

### Audio fails
```bash
python3 -c "import pyaudio; print('OK')"
sudo apt-get install portaudio19-dev
pip3 install --upgrade pyaudio
```

### No messages
```bash
mosquitto_sub -h localhost -t '#' -v
```

## File Locations

```
/opt/sentient-core/services/
├── perception.py              # Main service
├── perception.service         # Systemd config
├── test_perception.py         # Test suite
├── install_perception.sh      # Installer
├── PERCEPTION_README.md       # Full docs
└── QUICKSTART.md             # This file
```

## Next Steps

1. Review full documentation: `PERCEPTION_README.md`
2. Check implementation details: `IMPLEMENTATION_SUMMARY.md`
3. Run tests: `python3 test_perception.py`
4. Deploy: `sudo systemctl start perception`

## Support

All components production-ready:
- ✅ Async MQTT (aiomqtt)
- ✅ Audio monitoring (PyAudio)
- ✅ Time awareness
- ✅ Threat detection
- ✅ Error handling
- ✅ Auto-reconnect
- ✅ Systemd integration
- ✅ Complete tests
- ✅ No placeholders

**Ready for production deployment.**
