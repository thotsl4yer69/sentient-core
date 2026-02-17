# Memory System - Quick Reference

## 30-Second Start

```bash
cd /opt/sentient-core/services
./install_memory.sh          # Install everything
python3 memory.py            # Run demo
```

## Python API (High-Level)

```python
from memory_api import MemoryAPI
import asyncio

async def main():
    async with MemoryAPI() as api:
        # Store conversation
        await api.remember("user message", "assistant response")

        # Search memories
        results = await api.recall("query", limit=5)
        print(results)

        # Get recent context
        context = await api.get_context()
        print(context)

        # Core facts
        await api.know("name", "Jack")
        name = await api.know("name")
        all_facts = await api.get_all_facts()

asyncio.run(main())
```

## Python API (Low-Level)

```python
from memory import MemorySystem
import asyncio

async def main():
    memory = MemorySystem()
    await memory.connect()

    # Store with automatic importance
    interaction = await memory.store_interaction(
        "I prefer Python for AI projects",
        "I'll remember that!"
    )

    # Search with filters
    results = await memory.search_memories(
        query="programming preferences",
        limit=5,
        min_similarity=0.6,
        tags=["about-jack"]
    )

    # Core memory
    await memory.update_core_memory("language", "Python")
    lang = await memory.get_core_memory("language")

    # Context
    context = await memory.get_working_context(limit=20)

    # Stats
    stats = await memory.get_memory_stats()

    await memory.disconnect()

asyncio.run(main())
```

## CLI Commands

```bash
# Store interaction
./memory_cli.py store "User message" "Assistant response"

# Search
./memory_cli.py search "query" --limit 5 --format json

# Get context
./memory_cli.py context

# Core memory - set
./memory_cli.py know name '"Jack"'
./memory_cli.py know interests '["AI", "robotics"]'

# Core memory - get
./memory_cli.py know name

# All facts
./memory_cli.py facts

# Statistics
./memory_cli.py stats

# Consolidation
./memory_cli.py consolidate

# Export
./memory_cli.py export memories.json
```

## Service Management

```bash
# Status
sudo systemctl status sentient-memory

# Start/Stop
sudo systemctl start sentient-memory
sudo systemctl stop sentient-memory

# Enable/Disable autostart
sudo systemctl enable sentient-memory
sudo systemctl disable sentient-memory

# Logs
sudo journalctl -u sentient-memory -f
sudo journalctl -u sentient-memory -n 100
```

## Configuration

### Environment Variables
```bash
export REDIS_HOST=localhost
export REDIS_PORT=6379
export MQTT_BROKER=localhost
export MQTT_PORT=1883
```

### Memory Thresholds (edit memory.py)
```python
WORKING_MAX_SIZE = 20          # Max recent interactions
WORKING_TTL = 3600             # 1 hour in seconds
EPISODIC_MIN_IMPORTANCE = 0.5  # Threshold for auto-storage
```

## MQTT Events

### Subscribe to All Events
```bash
mosquitto_sub -t "sentient/memory/event" -v
```

### Event Structure
```json
{
  "type": "interaction_stored",
  "timestamp": 1706543210.5,
  "data": {
    "interaction_id": "abc123",
    "importance": 0.75
  }
}
```

## Common Patterns

### Store and Search
```python
# Store
await api.remember("I work best in the evening", "Noted!")

# Search related
results = await api.recall("work schedule")
```

### Context-Aware Response
```python
# Get context
recent = await api.get_context()

# Get relevant memories
relevant = await api.recall(user_query, limit=3)

# Combine for response
response = generate_response(user_query, recent, relevant)
```

### Core Facts Management
```python
# Set nested facts
await api.know("preferences.language", "Python")
await api.know("preferences.work_time", "evening")
await api.know("interests", ["AI", "robotics", "automation"])

# Get specific fact
work_time = await api.know("preferences.work_time")

# Get all facts
all_facts = await api.get_all_facts()
```

### Time-Filtered Search
```python
import time

# Last 24 hours
now = time.time()
yesterday = now - 86400

results = await memory.search_memories(
    "topic",
    time_range=(yesterday, now)
)
```

### Tag-Filtered Search
```python
results = await memory.search_memories(
    "preferences",
    tags=["about-jack", "work"]
)
```

## Testing

```bash
# Run all tests
python3 test_memory.py

# Run demo
python3 memory.py

# Run integration examples
python3 memory_integration_example.py
```

## Troubleshooting

### Redis Not Running
```bash
sudo systemctl start redis-server
redis-cli ping  # Should return PONG
```

### MQTT Not Running
```bash
sudo systemctl start mosquitto
mosquitto_sub -t test  # Should not error
```

### Module Not Found
```bash
pip3 install -r /opt/sentient-core/requirements.txt
```

### Check Installation
```bash
python3 -c "from memory import MemorySystem; print('OK')"
```

## Performance Tips

1. **Batch operations**: Store multiple interactions before searching
2. **Use appropriate similarity threshold**: Higher = fewer but better results
3. **Filter by tags**: Faster than full semantic search
4. **Use time ranges**: Limit search scope when possible
5. **Monitor episodic size**: Consider cleanup if >10k memories

## Key Files

| File | Purpose |
|------|---------|
| memory.py | Core implementation |
| memory_api.py | High-level API |
| memory_cli.py | Command-line tool |
| MEMORY_README.md | Full documentation |
| install_memory.sh | Installer |

## Getting Help

1. Check MEMORY_README.md for details
2. See examples in memory_integration_example.py
3. Review test_memory.py for usage patterns
4. Check logs: `sudo journalctl -u sentient-memory -f`

## One-Liners

```bash
# Quick stats
python3 -c "import asyncio; from memory_api import MemoryAPI; asyncio.run((lambda: MemoryAPI().stats())())"

# Store interaction
./memory_cli.py store "msg" "resp"

# Search
./memory_cli.py search "query" -l 5

# Export all
./memory_cli.py export /tmp/memories.json
```
