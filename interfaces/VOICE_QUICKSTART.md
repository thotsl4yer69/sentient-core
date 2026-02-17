# Voice-First Mode - Quick Start Guide

## Installation (5 minutes)

```bash
cd /opt/sentient-core/interfaces
./install_voice.sh
```

This installs everything needed and runs tests.

## Start Services

```bash
# Start MQTT broker
sudo systemctl start mosquitto

# Start voice services
sudo systemctl start sentient-wake-word
sudo systemctl start sentient-whisper-stt
sudo systemctl start sentient-piper-tts
sudo systemctl start sentient-voice
```

## Check Status

```bash
# View voice mode status
sudo systemctl status sentient-voice

# Watch logs in real-time
sudo journalctl -u sentient-voice -f
```

## Test It

1. Say: **"Hey Cortana"**
2. Wait for alert (avatar responds)
3. Speak your message
4. Listen to Cortana's response

## Common Commands

### Service Control

```bash
# Start
sudo systemctl start sentient-voice

# Stop
sudo systemctl stop sentient-voice

# Restart
sudo systemctl restart sentient-voice

# Enable at boot
sudo systemctl enable sentient-voice

# Disable at boot
sudo systemctl disable sentient-voice
```

### Logs

```bash
# Real-time logs
sudo journalctl -u sentient-voice -f

# Last 100 lines
sudo journalctl -u sentient-voice -n 100

# Logs from today
sudo journalctl -u sentient-voice --since today

# Log file
tail -f /var/log/sentient/voice.log
```

### MQTT Monitoring

```bash
# Watch all sentient topics
mosquitto_sub -h localhost -t 'sentient/#' -v

# Watch only voice topics
mosquitto_sub -h localhost -t 'sentient/wake/#' -t 'sentient/stt/#' -t 'sentient/tts/#' -v

# Test wake word
mosquitto_pub -h localhost -t 'sentient/wake/detected' -m '{"confidence": 0.9}'
```

## Troubleshooting

### No wake word detection

```bash
# Check wake word service
sudo systemctl status sentient-wake-word
sudo journalctl -u sentient-wake-word -f

# Test microphone
arecord -d 3 -f cd test.wav
aplay test.wav
```

### No audio recording

```bash
# List audio devices
arecord -l

# Test PyAudio
python3 -c "import pyaudio; p = pyaudio.PyAudio(); print(p.get_default_input_device_info())"
```

### No transcription

```bash
# Check STT service
sudo systemctl status sentient-whisper-stt
sudo journalctl -u sentient-whisper-stt -f

# Check MQTT
mosquitto_sub -h localhost -t 'sentient/stt/#' -v
```

### No TTS response

```bash
# Check TTS service
sudo systemctl status sentient-piper-tts
sudo journalctl -u sentient-piper-tts -f

# Check conversation service
sudo systemctl status sentient-conversation
```

### High CPU usage

Check wake word service (runs continuously):

```bash
top -p $(pgrep -f wake_word.py)
```

Adjust detection parameters if needed.

## Configuration Files

| File | Purpose |
|------|---------|
| `/opt/sentient-core/interfaces/voice.py` | Main voice service |
| `/opt/sentient-core/services/wake_word.py` | Wake word detection |
| `/opt/sentient-core/services/whisper_stt.py` | Speech-to-text |
| `/opt/sentient-core/services_websocket/piper_tts_service.py` | Text-to-speech |
| `/etc/systemd/system/sentient-voice.service` | Systemd service config |

## Key Parameters

In `/opt/sentient-core/interfaces/voice.py`:

```python
MAX_RECORDING_SECONDS = 10      # Max recording time
SILENCE_THRESHOLD_MS = 1500     # Silence before stopping
VAD_AGGRESSIVENESS = 3          # VAD sensitivity (0-3)
```

## MQTT Flow

```
User: "Hey Cortana"
  ↓
sentient/wake/detected (wake word service)
  ↓
sentient/avatar/state = "alert" (voice mode)
  ↓
[Recording with VAD]
  ↓
sentient/stt/audio/input (voice mode → STT)
  ↓
sentient/stt/output (STT → voice mode)
  ↓
sentient/persona/chat/input (voice mode → conversation)
  ↓
sentient/persona/response (conversation → voice mode)
  ↓
[TTS automatically triggered]
  ↓
sentient/tts/started, sentient/tts/completed
  ↓
sentient/avatar/state = "idle"
```

## Performance Tips

1. **Use GPU for Whisper**: Faster transcription
   - Set `WHISPER_DEVICE=cuda` in STT service

2. **Smaller models**: Lower latency
   - Whisper: Use `tiny.en` or `base.en`
   - Piper: Use smaller voice model

3. **Adjust VAD**: Less waiting
   - Increase `VAD_AGGRESSIVENESS` for quicker cutoff
   - Decrease `SILENCE_THRESHOLD_MS` for faster response

## Full Documentation

See `/opt/sentient-core/interfaces/VOICE_MODE.md` for complete documentation.
