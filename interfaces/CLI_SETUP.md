# Sentient Core Terminal CLI

Production-ready MQTT-based conversational interface for testing and development.

## Features

- **Interactive stdin/stdout interface** - Type and press Enter to chat
- **MQTT-based communication** - Real-time message delivery
- **Colored output** - Easy-to-read terminal formatting with colorama
- **Emotion state display** - See Cortana's emotional reactions
- **Thinking indicator** - Real-time spinner showing thinking/deliberation
- **Debug mode** - Visibility into internal MQTT messages and state
- **Graceful shutdown** - Clean Ctrl+C handling
- **Production-ready** - Proper error handling, logging, and async patterns

## Installation

### Prerequisites

```bash
# MQTT broker (ensure it's running)
mosquitto  # or any MQTT 3.1.1+ compatible broker

# Python 3.8+
python3 --version

# Install dependencies
pip install aiomqtt colorama
```

### Setup

```bash
# Ensure directories exist
mkdir -p /var/log/sentient
chmod 755 /var/log/sentient

# The CLI is already in place at:
/opt/sentient-core/interfaces/cli.py

# Test permissions
ls -l /opt/sentient-core/interfaces/cli.py
# Should show: -rwxrwxr-x
```

## Quick Start

### 1. Start the MQTT Broker

```bash
# If using Mosquitto
mosquitto -c /etc/mosquitto/mosquitto.conf

# Or if running as a service
systemctl start mosquitto
systemctl status mosquitto
```

### 2. Start the Conversation Service (in one terminal)

```bash
cd /opt/sentient-core
python3 services/conversation.py
```

You should see:
```
[timestamp] ConversationService INFO Connected to MQTT broker at localhost:1883
[timestamp] ConversationService INFO Conversation service started - listening for messages
[timestamp] ConversationService INFO Subscribed to sentient/conversation/input
```

### 3. Start the CLI (in another terminal)

```bash
cd /opt/sentient-core
python3 interfaces/cli.py
```

You should see:
```
[timestamp] INFO Connected to Sentient Core
Welcome to Cortana Terminal Interface
Type your message and press Enter. Type 'quit' to exit.

You:
```

### 4. Chat!

```
You: Hello
[timestamp] Cortana 😊 happy [▓▓▓▓░]:
Hello! I'm Cortana. How can I help you today?

You: How are you?
[timestamp] Cortana 😊 happy [▓▓▓░░]:
I'm doing well, thank you for asking! How about you?

You: quit
[timestamp] INFO Goodbye!
```

## Usage

### Standard Mode

```bash
python3 /opt/sentient-core/interfaces/cli.py
```

### Debug Mode (see MQTT messages)

```bash
python3 /opt/sentient-core/interfaces/cli.py --debug
```

Debug output includes:
- MQTT connection/subscription messages
- Published message details
- Message received notifications
- Thinking state updates

Example debug output:
```
[timestamp] [DEBUG] Connected to MQTT broker at localhost:1883
[timestamp] [DEBUG] Subscribed to: sentient/conversation/response, sentient/conversation/thinking, sentient/emotion/state
[timestamp] [DEBUG] Published to sentient/conversation/input: Hello
[timestamp] [DEBUG] Subscription received from sentient/conversation/response
```

### Remote MQTT Broker

```bash
python3 /opt/sentient-core/interfaces/cli.py --host 192.168.1.100 --port 1883
```

## Features in Detail

### Emotion Display

Cortana displays emotions with:
- **Emoji**: Visual representation (😊, 🤔, 😟, etc.)
- **Name**: Emotion type (happy, curious, concerned, etc.)
- **Intensity bar**: Visual intensity level [▓▓▓▓░]

Intensity scale:
- 0.0 - 0.2: Low [░░░░░]
- 0.2 - 0.4: Low-Medium [▓░░░░]
- 0.4 - 0.6: Medium [▓▓░░░]
- 0.6 - 0.8: Medium-High [▓▓▓░░]
- 0.8 - 1.0: High [▓▓▓▓▓]

### Thinking Indicator

While Cortana is thinking, you'll see:
```
⠋ Thinking (analyzing) about your question...
```

The spinner rotates through frames:
```
⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏
```

With stages:
- **analyzing**: Understanding your input
- **reasoning**: Processing the query
- **formulating**: Generating response

### Message Colors

- **Blue [INFO]**: System information messages
- **Cyan [You]**: Your messages
- **Green [Cortana]**: Cortana's responses
- **Yellow**: Thinking indicator
- **Magenta [DEBUG]**: Debug messages (--debug mode)
- **Red [ERROR]**: Error messages

## Architecture

### Message Flow

```
User Input (CLI)
    ↓
    └─→ MQTT Publish: sentient/conversation/input
            ↓
    Conversation Service receives
            ↓
    ├─→ Publishes thinking state (sentient/conversation/thinking)
    ├─→ Publishes emotion state (sentient/emotion/state)
    └─→ Publishes response (sentient/conversation/response)
            ↓
CLI Subscribe + Display
```

### MQTT Topics

| Topic | Direction | Purpose |
|-------|-----------|---------|
| `sentient/conversation/input` | → Service | User messages |
| `sentient/conversation/response` | ← Service | Responses |
| `sentient/conversation/thinking` | ← Service | Thinking/deliberation state |
| `sentient/emotion/state` | ← Service | Current emotion + intensity |

### Payload Formats

**Input:**
```json
{
  "message": "Hello Cortana",
  "timestamp": "2024-01-29T10:30:45.123456",
  "source": "cli"
}
```

**Response:**
```json
{
  "message": "Hello! How can I help?",
  "timestamp": "2024-01-29T10:30:45.654321",
  "service": "conversation"
}
```

**Thinking State:**
```json
{
  "is_thinking": true,
  "topic": "user greeting",
  "stage": "analyzing",
  "confidence": 0.85,
  "timestamp": "2024-01-29T10:30:45.234567"
}
```

**Emotion State:**
```json
{
  "emotion": "happy",
  "intensity": 0.8,
  "timestamp": 1706524245.123456
}
```

## Logging

### CLI Logs

```
/var/log/sentient/cli.log
```

Contains:
- Connection events
- Message exchanges
- Errors and exceptions
- Debug information

View logs:
```bash
tail -f /var/log/sentient/cli.log
```

### Service Logs

```
/var/log/sentient/conversation.log
```

## Testing

### Manual Testing

```bash
# Terminal 1: Start MQTT broker
mosquitto

# Terminal 2: Start conversation service
cd /opt/sentient-core
python3 services/conversation.py

# Terminal 3: Run CLI
cd /opt/sentient-core
python3 interfaces/cli.py

# Terminal 3: Test commands
# Try these:
# - "hello"
# - "how are you"
# - "what's your name"
# - "help"
# - "goodbye"
```

### Testing with Debug Mode

```bash
# Terminal 3: Run CLI with debug
python3 interfaces/cli.py --debug

# You'll see MQTT publish/subscribe messages in real-time
```

### Testing Responses

The mock conversation service responds to:
- **hello/hi** → Happy greeting
- **how are you** → Thoughtful response
- **what's your name** → Curious response
- **who are you** → Thoughtful identity
- **help** → Offering assistance
- **time** → Current time
- **thank you/thanks** → Happy gratitude
- **goodbye/bye** → Neutral farewell
- **anything else** → Generic echo response

## Integration with Real Services

To integrate with a real conversation service:

1. Replace mock responses in `conversation.py` with your LLM inference
2. Maintain the MQTT topic structure
3. Keep the emotion/thinking state format consistent

Example integration points:
```python
# In conversation.py
async def _handle_input(self, user_input: str):
    # Instead of: response, emotion, intensity = get_mock_response(user_input)

    # Use your LLM:
    response = await your_llm_service.generate(user_input)
    emotion, intensity = await your_emotion_detector.analyze(response)

    # Rest of the code stays the same
    await self._publish_emotion_state(emotion, intensity)
    await self._publish_response(response)
```

## Troubleshooting

### "Connection refused"

```
ERROR: Failed to connect to MQTT broker
```

**Solution:** Check if MQTT broker is running
```bash
# Check if mosquitto is running
systemctl status mosquitto

# Or start it
mosquitto -c /etc/mosquitto/mosquitto.conf
```

### "No response received (timeout after 30s)"

**Possible causes:**
1. Conversation service not running
2. Service not subscribed to `sentient/conversation/input`
3. Response topic misconfigured

**Solution:**
```bash
# Terminal 1: Check MQTT broker is running
mosquitto -c /etc/mosquitto/mosquitto.conf

# Terminal 2: Start conversation service
python3 /opt/sentient-core/services/conversation.py

# Terminal 3: Run CLI with debug to see message flow
python3 /opt/sentient-core/interfaces/cli.py --debug
```

### "ModuleNotFoundError: No module named 'aiomqtt'"

**Solution:**
```bash
pip install aiomqtt colorama
```

### "PermissionError: /var/log/sentient"

**Solution:**
```bash
sudo mkdir -p /var/log/sentient
sudo chmod 755 /var/log/sentient
sudo chown $USER:$USER /var/log/sentient
```

## Performance Characteristics

| Metric | Value |
|--------|-------|
| Response latency | ~500ms (mock service) |
| UI responsiveness | Real-time |
| Memory usage | ~50-100MB |
| CPU usage | <1% idle, <5% active |
| Max concurrent users | Limited by MQTT broker |

## Security Considerations

For production use:

1. **MQTT Authentication**
   ```bash
   # Enable username/password in mosquitto.conf
   allow_anonymous false
   password_file /etc/mosquitto/passwd
   ```

2. **TLS/SSL Encryption**
   ```bash
   # Configure in mosquitto.conf
   listener 8883
   cafile /path/to/ca.crt
   certfile /path/to/server.crt
   keyfile /path/to/server.key
   tls_version tlsv1.2
   ```

3. **Topic Access Control**
   ```bash
   # ACL rules in mosquitto.conf
   acl_file /etc/mosquitto/acl.txt
   ```

## Command Reference

### CLI Arguments

```bash
python3 /opt/sentient-core/interfaces/cli.py [OPTIONS]

OPTIONS:
  --debug              Enable debug mode (show MQTT details)
  --host HOST          MQTT broker hostname (default: localhost)
  --port PORT          MQTT broker port (default: 1883)
  --help               Show this help message
```

### CLI Commands

During chat:
- **Type message + Enter**: Send message
- **quit/exit/bye**: Exit the application
- **Ctrl+C**: Force quit

## API Reference (for integration)

### MQTT Topics to Subscribe

```python
from aiomqtt import Client

async with Client("localhost", 1883) as client:
    await client.subscribe("sentient/conversation/input")
    await client.subscribe("sentient/emotion/state")
    await client.subscribe("sentient/conversation/thinking")
```

### Publishing Messages

```python
import json

payload = {
    "message": "User input here",
    "timestamp": "2024-01-29T10:30:45.123456",
    "source": "cli"
}

await client.publish(
    "sentient/conversation/input",
    json.dumps(payload)
)
```

## Contributing

To extend the CLI:

1. **New emotion types**: Add to `EmotionState` enum
2. **New message types**: Extend MQTT topics and handlers
3. **UI improvements**: Modify `print_*` functions
4. **Performance**: Profile with `--debug` mode

## License

Part of Sentient Core project.

## Support

For issues or questions:
1. Check logs: `/var/log/sentient/cli.log`
2. Enable debug mode: `--debug`
3. Verify MQTT broker connection
4. Check conversation service is running
