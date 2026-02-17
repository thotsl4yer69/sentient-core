# Notification Service Documentation

Production-ready notification system for Sentient Core with **ntfy.sh** integration, rate limiting, and priority-based delivery.

## Features

- **ntfy.sh Integration**: Send push notifications to mobile devices via ntfy.sh
- **Priority Levels**: INFO, ALERT, URGENT with different rate limits
- **Rate Limiting**: Prevent notification spam
  - INFO: max 10/hour
  - ALERT: max 20/hour
  - URGENT: unlimited
- **Redis Configuration**: Store and retrieve ntfy topic URL
- **Async/Await**: Full async support using aiohttp and aioredis
- **Error Handling**: Proper exception hierarchy and error recovery
- **Audit Logging**: Track sent notifications in Redis
- **Production Ready**: Logging, signal handling, graceful shutdown

## Quick Start

### Installation

```bash
# Install dependencies
pip install aiohttp redis aioredis

# Ensure Redis is running
redis-server
```

### Basic Usage

```python
import asyncio
from notifications import initialize, send_notification

async def main():
    # Initialize with your ntfy.sh topic URL
    service = await initialize(
        ntfy_topic_url="https://ntfy.sh/my-sentient-core"
    )

    try:
        # Send a notification
        await send_notification(
            message="System ready",
            priority="INFO",
            title="Startup"
        )
    finally:
        await service.shutdown()

asyncio.run(main())
```

### Using Global Service Pattern (Recommended)

```python
import asyncio
from notifications import initialize, send_notification

async def main():
    # Initialize once at application startup
    await initialize(ntfy_topic_url="https://ntfy.sh/my-sentient-core")

    # Send notifications from anywhere in your code
    await send_notification(
        message="Event occurred",
        priority="ALERT",
        title="Alert",
        tags=["event", "monitoring"]
    )

asyncio.run(main())
```

## API Reference

### Initialization

#### `initialize(ntfy_topic_url: Optional[str]) -> NotificationService`

Initialize the global notification service.

**Parameters:**
- `ntfy_topic_url` (optional): Full URL to your ntfy.sh topic
  - Example: `https://ntfy.sh/my-sentient-core`
  - If not provided, will try to load from Redis config

**Returns:** Initialized `NotificationService` instance

**Raises:** `RuntimeError` if initialization fails

```python
service = await initialize(ntfy_topic_url="https://ntfy.sh/my-topic")
```

### Sending Notifications

#### `send_notification(message, priority="INFO", title=None, tags=None) -> bool`

Send a notification via ntfy.sh.

**Parameters:**
- `message` (str, required): Notification body text
- `priority` (str): Priority level
  - `"INFO"` (default): Low priority, rate limited to 10/hour
  - `"ALERT"`: Medium priority, rate limited to 20/hour
  - `"URGENT"`: High priority, unlimited
- `title` (str, optional): Notification title (shows on mobile)
- `tags` (list, optional): List of tags for categorization

**Returns:** `True` if sent successfully

**Raises:**
- `NotificationError`: Base exception
- `RateLimitExceededError`: When rate limit exceeded
- `NtfyShError`: When ntfy.sh API fails

```python
# Simple notification
await send_notification("Process completed")

# With title and priority
await send_notification(
    message="High CPU usage detected",
    priority="ALERT",
    title="Performance Warning"
)

# With tags
await send_notification(
    message="System failure",
    priority="URGENT",
    title="Critical Alert",
    tags=["critical", "system", "error"]
)
```

### Configuration Management

#### `service.set_config(key: str, value: str) -> bool`

Set a configuration value in Redis.

```python
await service.set_config("ntfy:topic_url", "https://ntfy.sh/new-topic")
```

#### `service.get_config(key: str) -> Optional[str]`

Retrieve a configuration value from Redis.

```python
topic = await service.get_config("ntfy:topic_url")
```

### Rate Limit Status

#### `service.get_rate_limit_status() -> Dict[str, Any]`

Get current rate limit status for all priority levels.

**Returns:**
```python
{
    "INFO": {"current": 5, "limit": 10, "unlimited": False},
    "ALERT": {"current": 3, "limit": 20, "unlimited": False},
    "URGENT": {"current": 2, "limit": None, "unlimited": True}
}
```

```python
status = await service.get_rate_limit_status()
print(f"INFO notifications: {status['INFO']['current']}/{status['INFO']['limit']}")
```

### Audit Logging

#### `service.get_audit_log(limit: int = 50) -> list`

Retrieve recent notification history.

**Parameters:**
- `limit`: Maximum number of entries to return (default: 50)

**Returns:** List of notification entries with timestamps

```python
log = await service.get_audit_log(limit=10)
for entry in log:
    print(f"{entry['timestamp']}: {entry['title']} - {entry['priority']}")
```

### Shutdown

#### `service.shutdown()`

Gracefully shutdown the service, closing all connections.

```python
await service.shutdown()
```

## ntfy.sh Setup

### Create a Topic

1. Visit https://ntfy.sh
2. Choose a topic name (e.g., `my-sentient-core`)
3. Subscribe on your phone
4. Full URL: `https://ntfy.sh/my-sentient-core`

### Configure in Your Application

```python
# Option 1: Direct configuration
service = await initialize(
    ntfy_topic_url="https://ntfy.sh/my-sentient-core"
)

# Option 2: Via Redis configuration
service = await initialize()
await service.set_config("ntfy:topic_url", "https://ntfy.sh/my-sentient-core")
```

### Mobile Apps

- iOS: Download ntfy app from App Store
- Android: Download ntfy app from Google Play or F-Droid

## Rate Limiting

The service implements per-priority rate limiting:

| Priority | Limit | Window |
|----------|-------|--------|
| INFO | 10/hour | Rolling hour |
| ALERT | 20/hour | Rolling hour |
| URGENT | Unlimited | N/A |

When a rate limit is exceeded:
```python
try:
    await send_notification("Message", priority="INFO")
except RateLimitExceededError:
    print("Rate limit exceeded, try again later")
```

Rate limit counters reset every hour. Use `get_rate_limit_status()` to check current usage.

## Error Handling

### Exception Hierarchy

```python
NotificationError (base)
├── RateLimitExceededError
└── NtfyShError
```

### Example Error Handling

```python
from notifications import (
    send_notification,
    NotificationError,
    RateLimitExceededError,
    NtfyShError
)

try:
    await send_notification("Message")
except RateLimitExceededError as e:
    logger.warning(f"Notification rate limited: {e}")
except NtfyShError as e:
    logger.error(f"ntfy.sh API error: {e}")
except NotificationError as e:
    logger.error(f"Notification error: {e}")
```

## Logging

The service logs to:
- **Console**: `stderr` via `StreamHandler`
- **File**: `/var/log/sentient/notifications.log`

Log format: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`

```
2024-01-29 10:30:45,123 - Notifications - INFO - Redis connection established
2024-01-29 10:30:45,456 - Notifications - INFO - HTTP session initialized
2024-01-29 10:30:46,789 - Notifications - INFO - Notification sent successfully via ntfy.sh
```

## Configuration Storage

All configuration is stored in Redis with `config:` prefix:

| Key | Value | Example |
|-----|-------|---------|
| `config:ntfy:topic_url` | Full ntfy.sh URL | `https://ntfy.sh/my-topic` |
| `config:app:name` | Application name | `Sentient Core` |

Retrieve configuration:
```python
topic = await service.get_config("ntfy:topic_url")
app_name = await service.get_config("app:name")
```

## Audit Trail

All sent notifications are logged to `notifications:audit_log` in Redis (newest first).

```python
log = await service.get_audit_log(limit=20)
# Returns:
# [
#     {
#         "timestamp": "2024-01-29T10:30:46.123456",
#         "priority": "INFO",
#         "title": "Startup",
#         "message": "System initialized successfully",
#         "status": "sent",
#         "tags": []
#     },
#     ...
# ]
```

Maximum 1000 entries retained.

## Integration Examples

### Application Startup/Shutdown

```python
import asyncio
from notifications import initialize, send_notification

async def main():
    service = await initialize(
        ntfy_topic_url="https://ntfy.sh/my-sentient-core"
    )

    try:
        await send_notification(
            message="Application started",
            priority="INFO",
            title="Startup"
        )

        # Your application logic here
        await asyncio.sleep(5)

    finally:
        await send_notification(
            message="Application shutting down",
            priority="INFO",
            title="Shutdown"
        )
        await service.shutdown()

asyncio.run(main())
```

### Monitoring and Alerting

```python
async def monitor_system():
    service = await get_service()

    # Check CPU usage
    cpu_usage = get_cpu_usage()
    if cpu_usage > 80:
        await send_notification(
            message=f"CPU usage: {cpu_usage}%",
            priority="ALERT",
            title="High CPU Warning",
            tags=["performance", "cpu"]
        )

    # Check disk space
    disk_free = get_disk_free()
    if disk_free < 10:  # 10% free
        await send_notification(
            message=f"Disk space: {disk_free}% free",
            priority="URGENT",
            title="Low Disk Space",
            tags=["storage", "critical"]
        )
```

### Background Tasks

```python
async def background_notification_task():
    service = await get_service()

    while True:
        try:
            # Do periodic work
            result = await perform_work()

            if result.has_errors:
                await send_notification(
                    message=f"Task failed: {result.error}",
                    priority="ALERT",
                    title="Task Error"
                )
        except Exception as e:
            await send_notification(
                message=f"Critical error: {str(e)}",
                priority="URGENT",
                title="Critical Failure",
                tags=["error", "critical"]
            )

        # Wait before next iteration
        await asyncio.sleep(300)  # 5 minutes
```

## Production Deployment

### Environment Setup

```bash
# Ensure log directory exists and is writable
sudo mkdir -p /var/log/sentient
sudo chown $(whoami) /var/log/sentient

# Verify Redis is running
redis-cli ping
# Should return: PONG

# Verify dependencies
pip install aiohttp redis aioredis
```

### Configuration

```python
# In your application initialization
from notifications import initialize

async def app_startup():
    # Initialize with your ntfy.sh topic
    await initialize(
        ntfy_topic_url="https://ntfy.sh/your-production-topic"
    )

    # Optionally set additional config
    service = await get_service()
    await service.set_config("app:version", "1.0.0")
```

### Signal Handling

The service respects SIGINT and SIGTERM for graceful shutdown:

```python
import signal

def signal_handler(signum, frame):
    asyncio.create_task(service.shutdown())

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
```

## Testing

Run the example file:

```bash
python3 /opt/sentient-core/services/notifications_example.py
```

Or run individual examples:

```python
import asyncio
from notifications_example import example_basic_usage

asyncio.run(example_basic_usage())
```

## Troubleshooting

### Redis Connection Failed
- Verify Redis is running: `redis-cli ping`
- Check Redis host/port configuration in `notifications.py`
- Ensure firewall allows connection to `localhost:6379`

### ntfy.sh API Errors
- Verify ntfy topic URL is correct
- Check internet connectivity
- Verify topic exists: visit `https://ntfy.sh/your-topic` in browser
- Check for API rate limits from ntfy.sh

### Notifications Not Sending
- Check service is initialized: `service._initialized == True`
- Verify ntfy topic URL is set: `await service.get_config("ntfy:topic_url")`
- Check logs: `tail -f /var/log/sentient/notifications.log`
- Verify mobile app is subscribed to topic

### Rate Limiting Too Aggressive
- Check current usage: `await service.get_rate_limit_status()`
- Use URGENT priority for critical messages (unlimited)
- Wait for hour to expire for rate limit reset

## Performance

- **HTTP Timeout**: 10 seconds total, 5 seconds connection
- **Redis Connection Pool**: Default aioredis configuration
- **Concurrent Requests**: Up to 5 per host
- **Audit Log**: Stores last 1000 notifications
- **Rate Limit Tracking**: Hourly rolling window per priority level

## Security

- **HTTPS**: All ntfy.sh requests use SSL/TLS
- **No Authentication**: ntfy.sh topics are public
- **Data Retention**: Audit log limited to 1000 entries
- **Rate Limiting**: Prevents notification bombing

**Important**: Choose a unique, hard-to-guess topic name or use a private ntfy.sh instance for sensitive data.

## License

Part of Sentient Core. See LICENSE file for details.
