# Voice-First Mode for Sentient Core

Complete production-ready voice interaction system with wake word detection, speech-to-text, conversation processing, text-to-speech, and visual feedback.

## Overview

Voice-First Mode provides a complete hands-free interaction pipeline:

```
Wake Word → STT → Conversation → TTS → Visual Feedback
   ↓         ↓         ↓           ↓         ↓
 Alert    Listen   Process     Speak     Avatar
```

## Architecture

### Flow Diagram

```
1. Wake Word Detection (OpenWakeWord)
   ├─> Publish: sentient/wake/detected
   └─> Avatar: Alert state

2. Audio Recording (PyAudio + VAD)
   ├─> Record until silence or max duration
   ├─> Voice Activity Detection (WebRTC VAD)
   └─> Avatar: Listening state

3. Speech-to-Text (Whisper)
   ├─> Publish: sentient/stt/audio/input
   ├─> Receive: sentient/stt/output
   └─> Avatar: Processing state

4. Conversation (Cortana Persona)
   ├─> Publish: sentient/persona/chat/input
   ├─> Receive: sentient/persona/response
   └─> Avatar: Thinking state

5. Text-to-Speech (Piper)
   ├─> Automatic from persona service
   ├─> Monitor: sentient/tts/started, sentient/tts/completed
   └─> Avatar: Speaking state

6. Return to Idle
   └─> Avatar: Idle state (ready for next wake)
```

### Components

1. **voice.py** - Main voice-first mode service
   - MQTT event handling
   - Audio recording with VAD
   - State machine for conversation flow
   - Interrupt handling

2. **wake_word.py** - OpenWakeWord detection
   - Detects "Hey Cortana"
   - Sub-500ms latency
   - Publishes to `sentient/wake/detected`

3. **whisper_stt.py** - Whisper STT service
   - Faster Whisper with CUDA support
   - Subscribes to `sentient/stt/audio/input`
   - Publishes to `sentient/stt/output`

4. **Cortana Persona** - Conversation service
   - Receives on `sentient/persona/chat/input`
   - Publishes to `sentient/persona/response`
   - Automatically sends to TTS

5. **piper_tts_service.py** - Piper TTS
   - Receives from persona service
   - Publishes audio + phonemes
   - Events: `sentient/tts/started`, `sentient/tts/completed`

## Installation

### 1. Install System Dependencies

```bash
# Audio libraries
sudo apt-get update
sudo apt-get install -y \
    portaudio19-dev \
    python3-pyaudio \
    libportaudio2

# MQTT broker
sudo apt-get install -y mosquitto mosquitto-clients
```

### 2. Install Python Dependencies

```bash
cd /opt/sentient-core
pip install -r requirements-voice.txt
```

Or manually:

```bash
pip install aiomqtt paho-mqtt PyAudio webrtcvad faster-whisper numpy
```

### 3. Test Installation

```bash
python3 /opt/sentient-core/interfaces/test_voice.py
```

This will verify:
- All Python modules can be imported
- Audio input device is available
- VAD can be initialized
- MQTT broker is reachable

### 4. Install systemd Service

```bash
sudo cp /opt/sentient-core/systemd/sentient-voice.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sentient-voice
```

## Configuration

### Environment Variables

Set in `/etc/systemd/system/sentient-voice.service` or `.env`:

```bash
MQTT_BROKER=localhost        # MQTT broker hostname
MQTT_PORT=1883               # MQTT broker port
MQTT_USER=sentient           # MQTT username
MQTT_PASS=sentient1312      # MQTT password
```

### Audio Configuration

In `voice.py`:

```python
SAMPLE_RATE = 16000                # Audio sample rate (16kHz standard)
CHANNELS = 1                       # Mono audio
CHUNK_DURATION_MS = 30             # VAD frame size (10, 20, or 30ms)
MAX_RECORDING_SECONDS = 10         # Maximum recording duration
SILENCE_THRESHOLD_MS = 1500        # Stop after 1.5s of silence
VAD_AGGRESSIVENESS = 3             # VAD sensitivity (0-3, higher = more aggressive)
```

## Usage

### Start the Service

```bash
# Start manually for testing
python3 /opt/sentient-core/interfaces/voice.py

# Or via systemd
sudo systemctl start sentient-voice
sudo systemctl status sentient-voice
```

### View Logs

```bash
# Real-time logs
sudo journalctl -u sentient-voice -f

# Recent logs
sudo journalctl -u sentient-voice -n 100

# Log file
tail -f /var/log/sentient/voice.log
```

### Interaction Flow

1. **Say wake word**: "Hey Cortana"
2. **Wait for alert**: Avatar shows alert state
3. **Speak your message**: System records until you stop speaking
4. **Wait for response**: Avatar shows thinking → speaking states
5. **Listen to Cortana**: Audio plays back with lip-sync

### Interrupt While Speaking

If Cortana is speaking, you can interrupt by saying the wake word again:

1. Say "Hey Cortana" while Cortana is speaking
2. Current TTS playback stops immediately
3. New recording starts
4. Process continues with new input

## MQTT Topics

### Subscribed Topics

| Topic | Purpose | Payload |
|-------|---------|---------|
| `sentient/wake/detected` | Wake word detected | `{"confidence": float, "timestamp": str}` |
| `sentient/stt/output` | Transcription result | `{"text": str, "language": str, "confidence": float}` |
| `sentient/persona/response` | Cortana's response | `{"text": str, "emotion": str}` |
| `sentient/tts/started` | TTS playback started | `{"id": str, "text": str}` |
| `sentient/tts/completed` | TTS playback done | `{"id": str}` |
| `sentient/tts/output` | TTS audio generated | `{"audio": base64, "phonemes": list}` |

### Published Topics

| Topic | Purpose | Payload |
|-------|---------|---------|
| `sentient/avatar/state` | Avatar visual state | `{"state": str, ...}` |
| `sentient/stt/audio/input` | Audio for transcription | `{"audio": {"data": base64, "format": "wav"}}` |
| `sentient/persona/chat/input` | Text for conversation | `{"text": str, "source": "voice_mode"}` |
| `sentient/tts/stop` | Stop TTS playback | `{"reason": str}` |

## Avatar States

The system publishes avatar state changes for visual feedback:

| State | Trigger | Metadata |
|-------|---------|----------|
| `idle` | Ready for wake word | `{"mode": "voice_first", "ready": true}` |
| `alert` | Wake word detected | `{"trigger": "wake_word", "confidence": float}` |
| `listening` | Recording audio | - |
| `processing` | Transcribing audio | `{"stage": "transcribing"}` |
| `thinking` | Processing conversation | `{"input": str}` |
| `responding` | Preparing response | `{"emotion": str, "text_preview": str}` |
| `speaking` | TTS playback active | `{"tts_id": str}` |
| `error` | Error occurred | `{"message": str}` |

## Troubleshooting

### No Audio Input Device

```bash
# List audio devices
arecord -l

# Test recording
arecord -d 3 -f cd test.wav
aplay test.wav
```

If no device found:
- Check USB microphone is plugged in
- Run `sudo apt-get install alsa-utils`
- Configure default device in `/etc/asound.conf`

### MQTT Connection Failed

```bash
# Check mosquitto is running
sudo systemctl status mosquitto

# Test MQTT
mosquitto_pub -h localhost -t test -m "hello"
mosquitto_sub -h localhost -t test

# Check MQTT logs
sudo journalctl -u mosquitto -f
```

### VAD Too Sensitive

Adjust `VAD_AGGRESSIVENESS` in `voice.py`:

```python
VAD_AGGRESSIVENESS = 2  # Less aggressive (0-3)
```

Or increase silence threshold:

```python
SILENCE_THRESHOLD_MS = 2000  # Wait 2s of silence
```

### Recording Cuts Off Too Soon

Increase silence threshold:

```python
SILENCE_THRESHOLD_MS = 2500  # 2.5 seconds
```

Or increase max duration:

```python
MAX_RECORDING_SECONDS = 15  # 15 seconds max
```

### Wake Word Not Detected

Check wake word service:

```bash
sudo systemctl status sentient-wake-word
sudo journalctl -u sentient-wake-word -f
```

Adjust detection threshold in `wake_word.py`:

```python
DETECTION_THRESHOLD = 0.3  # Lower = more sensitive (0.0-1.0)
```

### No Response from Cortana

Check all dependent services:

```bash
# Check STT service
sudo systemctl status sentient-whisper-stt

# Check persona service
sudo systemctl status sentient-conversation

# Check TTS service
sudo systemctl status sentient-piper-tts

# Check MQTT message flow
mosquitto_sub -h localhost -t 'sentient/#' -v
```

## Performance

### Latency Breakdown

| Stage | Expected Latency |
|-------|------------------|
| Wake word detection | < 500ms |
| Audio recording (with silence) | 1-5 seconds |
| STT (Whisper base.en) | 200-800ms |
| Conversation (Ollama) | 500-2000ms |
| TTS (Piper) | 100-500ms |
| **Total (typical)** | **2.5-8.5 seconds** |

### Resource Usage

| Component | CPU | Memory | Notes |
|-----------|-----|--------|-------|
| voice.py | 5-15% | ~50MB | Peaks during recording |
| wake_word.py | 15-25% | ~100MB | Continuous audio processing |
| whisper_stt.py | 30-80% | ~500MB | GPU: 10-20% if available |
| piper_tts | 20-40% | ~200MB | During synthesis |

### Optimization Tips

1. **Use GPU for Whisper**: Set `WHISPER_DEVICE=cuda` for 3-5x speedup
2. **Smaller Whisper model**: Use `tiny.en` or `base.en` for faster transcription
3. **Adjust VAD**: More aggressive VAD = shorter recordings = faster processing
4. **Piper voice**: Use smaller voice model for faster synthesis

## Integration

### With Avatar Renderer

Avatar renderer subscribes to `sentient/avatar/state` and updates visuals:

```python
# Example avatar state handler
def on_avatar_state(state: str, metadata: dict):
    if state == "alert":
        # Show alert animation
        show_wake_animation()
    elif state == "listening":
        # Show listening indicator
        show_audio_waveform()
    elif state == "speaking":
        # Show lip-sync animation
        sync_lips_to_phonemes(metadata["tts_id"])
```

### With Web Dashboard

Dashboard can monitor voice interaction via WebSocket:

```javascript
// Subscribe to avatar states
ws.on('avatar_state', (data) => {
  console.log('Avatar state:', data.state);
  updateAvatarUI(data);
});

// Manual voice input trigger
sendVoiceInput(text) {
  ws.send({
    type: 'voice_input',
    text: text
  });
}
```

### With Proactive Monitoring

Voice mode respects ongoing TTS playback from proactive alerts:

- If proactive alert is speaking, wake word is still detected
- Wake word interrupts proactive TTS
- User input takes priority

## Security

### Audio Privacy

- Audio is NOT stored permanently
- Temporary WAV files deleted immediately after processing
- Only transcribed text is sent to conversation service

### MQTT Authentication

Always use authentication in production:

```bash
# Create MQTT password file
sudo mosquitto_passwd -c /etc/mosquitto/passwd sentient

# Configure mosquitto
echo "allow_anonymous false" | sudo tee -a /etc/mosquitto/mosquitto.conf
echo "password_file /etc/mosquitto/passwd" | sudo tee -a /etc/mosquitto/mosquitto.conf

sudo systemctl restart mosquitto
```

### Resource Limits

Systemd service has built-in limits:

```ini
MemoryMax=512M        # Prevent memory leaks
CPUQuota=50%          # Limit CPU usage
NoNewPrivileges=true  # Security hardening
PrivateTmp=true       # Isolated /tmp
```

## API Reference

### VoiceFirstMode Class

Main service class for voice interaction.

#### Methods

**`__init__()`**
Initialize voice mode service.

**`async initialize()`**
Initialize audio, VAD, and MQTT components.

**`async record_audio_with_vad() -> Optional[bytes]`**
Record audio until silence detected or max duration.
Returns WAV file bytes or None.

**`async send_to_stt(audio_bytes: bytes)`**
Send audio to Whisper STT service.

**`async send_to_conversation(text: str)`**
Send transcribed text to conversation service.

**`async set_avatar_state(state: str, metadata: dict = None)`**
Update avatar visual state.

**`async run()`**
Main run loop. Call this to start the service.

**`async shutdown()`**
Graceful shutdown of all components.

#### Event Handlers

**`async handle_wake_detected(payload: dict)`**
Handle wake word detection event.

**`async handle_stt_output(payload: dict)`**
Handle STT transcription result.

**`async handle_persona_response(payload: dict)`**
Handle Cortana's response from persona service.

**`async handle_tts_started(payload: dict)`**
Handle TTS playback started event.

**`async handle_tts_completed(payload: dict)`**
Handle TTS playback completed event.

## Future Enhancements

### Planned Features

- [ ] Multi-turn conversation context
- [ ] Voice fingerprinting for speaker identification
- [ ] Emotion detection from voice tone
- [ ] Background noise filtering (RNNoise)
- [ ] Adaptive VAD based on environment
- [ ] Streaming TTS for lower latency
- [ ] Wake word personalization training
- [ ] Multiple wake word support
- [ ] Voice command shortcuts (bypass conversation)

### Performance Improvements

- [ ] Audio pipeline optimization (reduce copies)
- [ ] Parallel STT and conversation processing
- [ ] Caching common responses
- [ ] Wake word + VAD fusion
- [ ] Edge TPU acceleration for wake word

## License

Part of Sentient Core system.
Created: 2026-01-29

## Support

For issues or questions:
1. Check logs: `sudo journalctl -u sentient-voice -f`
2. Run test script: `python3 test_voice.py`
3. Verify MQTT: `mosquitto_sub -t 'sentient/#' -v`
4. Check audio: `arecord -l`
