# Notification Service API Reference

Complete API documentation for the Sentient Core Notification Service.

## Module: `notifications`

### Classes

#### `PriorityLevel` (Enum)

Priority levels for notifications with ntfy.sh values.

**Members:**
- `INFO = 2` - Low priority (rate limited to 10/hour)
- `ALERT = 4` - Medium priority (rate limited to 20/hour)
- `URGENT = 5` - High priority (unlimited)

```python
from notifications import PriorityLevel

# Access by name
priority = PriorityLevel.INFO
priority = PriorityLevel['ALERT']

# Get numeric value
assert PriorityLevel.URGENT.value == 5
```

#### `RateLimitConfig`

Configuration for rate limiting behavior.

**Attributes:**
- `LIMITS: Dict[PriorityLevel, Optional[int]]` - Maximum notifications per hour
- `TIME_WINDOW: int` - Rate limit window in seconds (3600 = 1 hour)

```python
from notifications import RateLimitConfig

# Check limits
assert RateLimitConfig.LIMITS[PriorityLevel.INFO] == 10
assert RateLimitConfig.LIMITS[PriorityLevel.ALERT] == 20
assert RateLimitConfig.LIMITS[PriorityLevel.URGENT] is None  # Unlimited

# Modify limits (if needed)
RateLimitConfig.LIMITS[PriorityLevel.INFO] = 5  # 5 per hour instead of 10
```

#### `NotificationService`

Main service class for sending notifications and managing configuration.

##### Constructor

```python
NotificationService(ntfy_topic_url: Optional[str] = None)
```

**Parameters:**
- `ntfy_topic_url` (optional): Full ntfy.sh topic URL
  - Example: `"https://ntfy.sh/my-sentient-core"`
  - If not provided, loads from Redis config or can be set later

**Example:**
```python
# With URL
service = NotificationService(
    ntfy_topic_url="https://ntfy.sh/my-topic"
)

# Without URL (load from Redis later)
service = NotificationService()
```

##### Methods

###### `initialize() -> bool`

Initialize Redis and HTTP connections.

**Returns:** `True` if successful, `False` on error

**Raises:** None (logs errors internally)

**Example:**
```python
if not await service.initialize():
    logger.error("Failed to initialize")
    return False
```

###### `set_config(key: str, value: str) -> bool`

Set a configuration value in Redis.

**Parameters:**
- `key` (str): Configuration key (e.g., `"ntfy:topic_url"`)
- `value` (str): Configuration value

**Returns:** `True` if successful, `False` on error

**Stored as:** `config:{key}` in Redis

**Example:**
```python
# Set ntfy topic URL
await service.set_config("ntfy:topic_url", "https://ntfy.sh/my-topic")

# Set custom configuration
await service.set_config("app:version", "1.0.0")
await service.set_config("app:environment", "production")
```

###### `get_config(key: str) -> Optional[str]`

Retrieve a configuration value from Redis.

**Parameters:**
- `key` (str): Configuration key

**Returns:** Configuration value or `None` if not found

**Example:**
```python
topic = await service.get_config("ntfy:topic_url")
if topic:
    print(f"Using topic: {topic}")
```

###### `send_notification(...) -> bool`

Send a notification via ntfy.sh.

**Signature:**
```python
async def send_notification(
    message: str,
    priority: str = "INFO",
    title: Optional[str] = None,
    tags: Optional[list] = None,
) -> bool
```

**Parameters:**
- `message` (str, required): Notification body text
  - Must be non-empty
  - Will be stripped of leading/trailing whitespace
  - Maximum length: ntfy.sh has no hard limit, but keep reasonable

- `priority` (str, optional): Priority level
  - Valid values: `"INFO"`, `"ALERT"`, `"URGENT"` (case-insensitive)
  - Default: `"INFO"`

- `title` (str, optional): Notification title
  - Shown on mobile devices
  - Default: `"Sentient Core Notification"`

- `tags` (list, optional): Categorization tags
  - Example: `["sensor", "temperature", "alert"]`
  - Displayed with notification on mobile

**Returns:** `True` if sent successfully

**Raises:**
- `NotificationError`: Base exception for any notification error
- `RateLimitExceededError`: When rate limit exceeded (subclass of `NotificationError`)
- `NtfyShError`: When ntfy.sh API fails (subclass of `NotificationError`)

**Example:**
```python
# Simple notification
try:
    await service.send_notification("System ready")
except NotificationError as e:
    logger.error(f"Failed to send: {e}")

# Full example with all parameters
await service.send_notification(
    message="Temperature sensor reading: 25.5°C",
    priority="ALERT",
    title="Temperature Alert",
    tags=["sensor", "temperature", "warning"]
)
```

**HTTP Details:**
- Makes POST request to the configured ntfy topic URL
- Headers:
  - `Title`: Notification title
  - `Priority`: Numeric priority value (2, 4, or 5)
  - `Tags`: Comma-separated tags
  - `Content-Type`: `text/plain; charset=utf-8`
- Body: Message text as UTF-8
- Timeout: 10 seconds total, 5 seconds connection

###### `get_rate_limit_status() -> Dict[str, Any]`

Get current rate limit status for all priority levels.

**Returns:** Dictionary with status per priority

**Response Format:**
```python
{
    "INFO": {
        "current": 5,      # Current count in this hour
        "limit": 10,       # Maximum allowed
        "unlimited": False
    },
    "ALERT": {
        "current": 2,
        "limit": 20,
        "unlimited": False
    },
    "URGENT": {
        "current": 1,
        "limit": None,
        "unlimited": True
    }
}
```

**Example:**
```python
status = await service.get_rate_limit_status()

info_remaining = status["INFO"]["limit"] - status["INFO"]["current"]
print(f"INFO: {info_remaining} remaining this hour")

if status["URGENT"]["unlimited"]:
    print("URGENT notifications are unlimited")
```

###### `get_audit_log(limit: int = 50) -> list`

Retrieve recent notification audit trail.

**Parameters:**
- `limit` (int): Maximum number of entries to return (default: 50)

**Returns:** List of notification entries (newest first)

**Entry Format:**
```python
{
    "timestamp": "2024-01-29T10:30:46.123456",  # ISO format UTC
    "priority": "INFO",                          # Priority level
    "title": "Notification Title",               # Title
    "message": "First 100 chars of message",     # Truncated message
    "status": "sent",                            # Status
    "tags": ["tag1", "tag2"]                     # Tags list
}
```

**Notes:**
- Returns last 1000 entries maximum (stored in Redis)
- Sorted newest first
- Uses FIFO with LTRIM to maintain max size

**Example:**
```python
# Get last 10 notifications
log = await service.get_audit_log(limit=10)

for entry in log:
    ts = entry["timestamp"]
    priority = entry["priority"]
    title = entry["title"]
    print(f"{ts} [{priority}] {title}")

# Find all URGENT notifications
urgent = [e for e in log if e["priority"] == "URGENT"]
print(f"Found {len(urgent)} URGENT notifications")
```

###### `shutdown()`

Gracefully shutdown the service, closing all connections.

**Returns:** None

**Closes:**
- HTTP session (aiohttp.ClientSession)
- Redis connection

**Sets:** `running = False`, `_initialized = False`

**Example:**
```python
try:
    # Do work
    await service.send_notification("Done")
finally:
    await service.shutdown()
```

### Functions (Module-Level)

These are convenience functions that work with a global service instance.

#### `initialize(ntfy_topic_url: Optional[str]) -> NotificationService`

Initialize the global notification service.

**Parameters:**
- `ntfy_topic_url` (optional): Full ntfy.sh topic URL

**Returns:** Initialized `NotificationService` instance

**Raises:** `RuntimeError` if initialization fails

**Example:**
```python
from notifications import initialize

service = await initialize(
    ntfy_topic_url="https://ntfy.sh/my-sentient-core"
)
```

#### `send_notification(...) -> bool`

Send a notification using the global service.

**Signature:**
```python
async def send_notification(
    message: str,
    priority: str = "INFO",
    title: Optional[str] = None,
    tags: Optional[list] = None,
) -> bool
```

**Parameters:** Same as `NotificationService.send_notification()`

**Returns:** `True` if successful

**Raises:**
- `NotificationError` and subclasses
- `RuntimeError` if service not initialized

**Example:**
```python
from notifications import initialize, send_notification

# Initialize once
await initialize(ntfy_topic_url="https://ntfy.sh/my-topic")

# Use throughout application
await send_notification("Event 1")
await send_notification("Event 2", priority="ALERT", title="Alert")
```

#### `get_service() -> NotificationService`

Get the global service instance.

**Returns:** Current `NotificationService` instance

**Raises:** `RuntimeError` if not initialized

**Example:**
```python
from notifications import get_service

service = await get_service()
status = await service.get_rate_limit_status()
```

### Exceptions

#### `NotificationError`

Base exception for all notification service errors.

**Subclasses:**
- `RateLimitExceededError` - Rate limit exceeded
- `NtfyShError` - ntfy.sh API error

```python
from notifications import NotificationError

try:
    await send_notification("Test")
except NotificationError as e:
    logger.error(f"Notification failed: {e}")
```

#### `RateLimitExceededError`

Raised when attempting to send notification that exceeds rate limit.

**Message:** `"Rate limit exceeded for {priority} notifications"`

```python
from notifications import RateLimitExceededError

try:
    await service.send_notification("Test", priority="INFO")
except RateLimitExceededError:
    # Too many INFO notifications this hour
    logger.warning("Rate limit exceeded, will retry later")
```

#### `NtfyShError`

Raised when ntfy.sh API returns an error.

**Possible Messages:**
- `"ntfy.sh API error {status}: {response}"`
- `"HTTP request failed: {error}"`
- `"ntfy.sh request timeout"`

```python
from notifications import NtfyShError

try:
    await service.send_notification("Test")
except NtfyShError as e:
    # Network or API error
    logger.error(f"ntfy.sh unavailable: {e}")
```

## Configuration Storage (Redis Keys)

All configuration is stored in Redis with `config:` prefix.

### Standard Configuration Keys

| Key | Example Value | Purpose |
|-----|---------------|---------|
| `config:ntfy:topic_url` | `https://ntfy.sh/my-topic` | ntfy.sh topic URL |
| `config:app:name` | `Sentient Core` | Application name |
| `config:app:version` | `1.0.0` | Application version |
| `config:app:environment` | `production` | Environment (dev/staging/prod) |

### Internal Redis Keys

These are managed by the service and should not be modified directly.

| Pattern | Purpose |
|---------|---------|
| `notifications:ratelimit:{PRIORITY}:{HOUR}` | Rate limit counter for priority in hour |
| `notifications:audit_log` | List of recent notifications (FIFO) |

## Complete Example

```python
#!/usr/bin/env python3
import asyncio
from notifications import (
    initialize,
    send_notification,
    get_service,
    NotificationError,
    RateLimitExceededError,
)

async def main():
    # Initialize service
    service = await initialize(
        ntfy_topic_url="https://ntfy.sh/my-sentient-core"
    )

    try:
        # Send startup notification
        await send_notification(
            message="Application started",
            priority="INFO",
            title="Startup"
        )

        # Do application work
        for i in range(5):
            await asyncio.sleep(1)

            # Send progress notification
            try:
                await send_notification(
                    message=f"Processing step {i+1}/5",
                    priority="INFO",
                    title="Progress"
                )
            except RateLimitExceededError:
                print("Rate limit exceeded, skipping notification")

        # Check status
        service_ref = await get_service()
        status = await service_ref.get_rate_limit_status()
        print(f"Rate limit status: {status}")

        # Send completion notification
        await send_notification(
            message="Application completed successfully",
            priority="INFO",
            title="Completion",
            tags=["success", "completion"]
        )

        # Get audit log
        log = await service_ref.get_audit_log(limit=10)
        print(f"Sent {len(log)} notifications this session")

    except NotificationError as e:
        print(f"Notification error: {e}")

    finally:
        service = await get_service()
        await service.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
```

## Integration with asyncio

The service is fully async and integrates seamlessly with asyncio applications.

```python
# In an async web framework (FastAPI, aiohttp, etc.)
from fastapi import FastAPI
from notifications import initialize, send_notification

app = FastAPI()

@app.on_event("startup")
async def startup():
    await initialize(ntfy_topic_url="https://ntfy.sh/my-api")

@app.get("/trigger-alert")
async def trigger_alert():
    try:
        await send_notification(
            message="Alert triggered from API",
            priority="ALERT",
            title="API Alert"
        )
        return {"status": "sent"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.on_event("shutdown")
async def shutdown():
    service = await get_service()
    await service.shutdown()
```

## Performance Characteristics

- **HTTP Requests**: ~100-500ms per notification (depends on ntfy.sh and network)
- **Rate Limit Check**: <1ms (Redis operation)
- **Audit Log Insert**: <2ms (Redis operation)
- **Memory Usage**: ~10-50MB depending on cache size
- **Concurrent Requests**: Up to 5 per host (configurable)

## Error Recovery

The service handles common errors gracefully:

| Error | Behavior | Recovery |
|-------|----------|----------|
| Redis unavailable | Rate limit check fails open (allows notification) | Try again when Redis is back |
| HTTP timeout (>10s) | Raises NtfyShError | Retry with backoff |
| API error (4xx/5xx) | Raises NtfyShError | Check topic URL, verify topic exists |
| Invalid priority | Raises NotificationError immediately | Check priority value |
| Empty message | Raises NotificationError immediately | Provide non-empty message |

## Logging

All service operations are logged to:
- **Console**: `stderr` via `logging.StreamHandler`
- **File**: `/var/log/sentient/notifications.log`

**Log Levels:**
- `INFO`: Successful operations, initialization
- `WARNING`: Rate limits exceeded, Redis unavailable
- `ERROR`: API failures, connection errors
- `DEBUG`: Detailed rate limit checks

**Example Log Output:**
```
2024-01-29 10:30:45,123 - Notifications - INFO - Redis connection established
2024-01-29 10:30:45,456 - Notifications - INFO - HTTP session initialized
2024-01-29 10:30:46,789 - Notifications - INFO - Notification sent successfully via ntfy.sh (priority=INFO, title=Startup)
2024-01-29 10:31:00,012 - Notifications - WARNING - Rate limit exceeded for INFO: 11/10 in current hour
```

## Thread Safety

**Not thread-safe.** Use one global instance per asyncio event loop.

If using multiple threads:
```python
# Each thread needs its own event loop and service instance
async def thread_worker():
    service = await initialize()
    try:
        await service.send_notification("From thread")
    finally:
        await service.shutdown()

# Run in thread with new event loop
import threading
thread = threading.Thread(
    target=lambda: asyncio.run(thread_worker()),
    daemon=True
)
thread.start()
```

## Best Practices

1. **Initialize once** at application startup
2. **Reuse the global service** throughout application
3. **Handle RateLimitExceededError** gracefully
4. **Shutdown cleanly** in finally blocks or exit handlers
5. **Use appropriate priorities** (don't spam INFO, reserve URGENT for critical)
6. **Add tags** to make notifications categorizable
7. **Keep messages reasonable length** (<500 chars recommended)
8. **Check rate limit status** before sending many notifications

## License

Part of Sentient Core. See LICENSE file for details.
