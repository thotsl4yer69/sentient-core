# Notification Service Integration Guide

How to integrate the Notification Service into Sentient Core and other applications.

## System Integration Points

### 1. MQTT Integration

Send notifications when MQTT events occur:

```python
# In your MQTT handler
from notifications import send_notification

async def on_mqtt_message(client, userdata, msg):
    """Handle MQTT message and optionally send notification"""
    
    # Parse message
    topic = msg.topic
    payload = msg.payload.decode()
    
    # Trigger notification on specific topics
    if topic.startswith("sentient/alert"):
        await send_notification(
            message=f"Alert on {topic}: {payload}",
            priority="ALERT",
            title="MQTT Alert",
            tags=["mqtt", "alert", topic]
        )
    elif topic.startswith("sentient/critical"):
        await send_notification(
            message=f"Critical on {topic}: {payload}",
            priority="URGENT",
            title="MQTT Critical",
            tags=["mqtt", "critical", topic]
        )
```

### 2. Wake Word Detection Integration

```python
# In wake_word.py detection handler
from notifications import send_notification

async def publish_detection(confidence: float):
    """Publish wake word detection and notify"""
    
    # Existing MQTT publish code...
    
    # Send notification for high confidence detections
    if confidence > 0.9:
        await send_notification(
            message=f"Wake word detected with {confidence:.1%} confidence",
            priority="INFO",
            title="Wake Word Detected",
            tags=["wake-word", "audio"]
        )
```

### 3. Error Monitoring Integration

```python
# Global error handler
import logging
from notifications import send_notification, RateLimitExceededError

class NotificationErrorHandler(logging.Handler):
    """Logging handler that sends critical errors as notifications"""
    
    async def emit(self, record):
        """Emit log record as notification if critical"""
        
        if record.levelno >= logging.ERROR:
            try:
                priority = "URGENT" if record.levelno >= logging.CRITICAL else "ALERT"
                await send_notification(
                    message=f"{record.name}: {record.getMessage()}",
                    priority=priority,
                    title=f"{record.levelname} in {record.module}",
                    tags=["error", record.module]
                )
            except RateLimitExceededError:
                # Don't escalate rate limit errors
                pass
            except Exception as e:
                logging.error(f"Failed to send error notification: {e}")

# Add to logging configuration
error_handler = NotificationErrorHandler()
error_handler.setLevel(logging.ERROR)
logging.getLogger().addHandler(error_handler)
```

### 4. Health Check Integration

```python
# Periodic health check that sends notifications
import asyncio
from notifications import send_notification, get_service

async def health_check_loop():
    """Periodically check system health and send notifications"""
    
    while True:
        try:
            # Check various health metrics
            cpu_usage = get_cpu_usage()
            memory_usage = get_memory_usage()
            disk_usage = get_disk_usage()
            
            # Send alerts for abnormal conditions
            if cpu_usage > 90:
                await send_notification(
                    message=f"CPU usage critical: {cpu_usage}%",
                    priority="URGENT",
                    title="High CPU Alert",
                    tags=["performance", "cpu"]
                )
            
            if memory_usage > 85:
                await send_notification(
                    message=f"Memory usage high: {memory_usage}%",
                    priority="ALERT",
                    title="High Memory Warning",
                    tags=["performance", "memory"]
                )
            
            if disk_usage > 90:
                await send_notification(
                    message=f"Disk usage critical: {disk_usage}%",
                    priority="URGENT",
                    title="Low Disk Space",
                    tags=["storage", "critical"]
                )
            
            # Wait before next check
            await asyncio.sleep(300)  # 5 minutes
            
        except Exception as e:
            logging.error(f"Health check error: {e}")
            await asyncio.sleep(60)

# Start in main application
async def main():
    # Initialize notification service
    await initialize(ntfy_topic_url="https://ntfy.sh/my-sentient-core")
    
    # Start health check task
    health_task = asyncio.create_task(health_check_loop())
    
    try:
        # Run application
        await asyncio.gather(
            main_application(),
            health_task
        )
    finally:
        health_task.cancel()
        service = await get_service()
        await service.shutdown()
```

### 5. Personality Module Integration

```python
# In personality module for state changes
from notifications import send_notification

async def update_personality_state(new_state: str):
    """Update personality state and notify"""
    
    old_state = get_current_state()
    
    # Update state...
    
    # Send notification on significant state changes
    if new_state == "alert" and old_state != "alert":
        await send_notification(
            message="Personality switched to ALERT mode",
            priority="ALERT",
            title="State Change",
            tags=["personality", "state-change"]
        )
    
    elif new_state == "critical" and old_state != "critical":
        await send_notification(
            message="Personality switched to CRITICAL mode",
            priority="URGENT",
            title="Critical State Change",
            tags=["personality", "critical"]
        )
```

### 6. API/Web Server Integration

```python
# In FastAPI or aiohttp application
from fastapi import FastAPI
from notifications import initialize, send_notification, get_service

app = FastAPI(title="Sentient Core API")

@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    await initialize(
        ntfy_topic_url="https://ntfy.sh/sentient-core-api"
    )
    await send_notification(
        message="Sentient Core API started",
        priority="INFO",
        title="API Startup"
    )

@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown services"""
    await send_notification(
        message="Sentient Core API shutting down",
        priority="INFO",
        title="API Shutdown"
    )
    service = await get_service()
    await service.shutdown()

@app.get("/api/events/{event_type}")
async def trigger_event(event_type: str, message: str = None):
    """Trigger a notification event"""
    try:
        await send_notification(
            message=message or f"Event triggered: {event_type}",
            priority="INFO",
            title=f"Event: {event_type}",
            tags=["api", "event", event_type]
        )
        return {"status": "sent"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.get("/api/alerts/{priority}")
async def create_alert(priority: str, message: str):
    """Create an alert via API"""
    try:
        await send_notification(
            message=message,
            priority=priority.upper(),
            title="API Alert",
            tags=["api", "alert"]
        )
        return {"status": "sent", "priority": priority}
    except Exception as e:
        return {"status": "error", "error": str(e)}, 400
```

## Configuration Management Workflow

### Initial Setup

```python
async def setup_notifications():
    """One-time setup for notifications"""
    
    service = await initialize()
    
    # Set required configuration
    await service.set_config(
        "ntfy:topic_url",
        "https://ntfy.sh/my-sentient-core"
    )
    
    # Set application info
    await service.set_config("app:name", "Sentient Core")
    await service.set_config("app:version", "1.0.0")
    await service.set_config("app:environment", "production")
    
    # Test notification
    try:
        await send_notification(
            message="Notification system configured and tested",
            priority="INFO",
            title="Setup Complete"
        )
        print("✓ Notification system ready")
    except Exception as e:
        print(f"✗ Configuration failed: {e}")
    
    await service.shutdown()

# Run setup
# asyncio.run(setup_notifications())
```

### Dynamic Configuration Updates

```python
async def update_config_command(key: str, value: str):
    """Command handler for configuration updates"""
    
    service = await get_service()
    
    try:
        success = await service.set_config(key, value)
        if success:
            await send_notification(
                message=f"Configuration updated: {key} = {value}",
                priority="INFO",
                title="Config Updated",
                tags=["config", "update"]
            )
        return {"status": "updated"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
```

## Deployment Integration

### Systemd Service Unit

```ini
# /etc/systemd/system/sentient-notifications.service
[Unit]
Description=Sentient Core Notification Service
After=network-online.target redis-server.service
Requires=redis-server.service

[Service]
Type=simple
User=sentient
WorkingDirectory=/opt/sentient-core
ExecStart=/usr/bin/python3 -m notifications
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Docker Integration

```dockerfile
# Dockerfile for Sentient Core with notifications
FROM python:3.11-slim

WORKDIR /opt/sentient-core

# Install dependencies
COPY services/requirements-notifications.txt .
RUN pip install -r requirements-notifications.txt

# Copy service
COPY services/notifications.py .

# Set up logging
RUN mkdir -p /var/log/sentient

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD redis-cli -h redis ping

CMD ["python3", "-m", "notifications"]
```

### Environment Variables (Optional)

```bash
# .env file or systemd service
NTFY_TOPIC_URL=https://ntfy.sh/my-sentient-core
NTFY_TOPIC_NAME=my-sentient-core
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
LOG_LEVEL=INFO
```

## Testing Integration

### Integration Test Suite

```python
# tests/test_notification_integration.py

import asyncio
import pytest
from notifications import initialize, send_notification, get_service

@pytest.mark.asyncio
async def test_notification_with_mqtt():
    """Test notification triggers on MQTT events"""
    await initialize(ntfy_topic_url="https://ntfy.sh/test")
    
    # Simulate MQTT event
    await send_notification(
        message="Test MQTT event",
        priority="INFO",
        title="Test Event"
    )
    
    # Verify audit log
    service = await get_service()
    log = await service.get_audit_log(limit=1)
    assert len(log) > 0
    assert log[0]["title"] == "Test Event"

@pytest.mark.asyncio
async def test_rate_limiting_integration():
    """Test rate limiting across multiple sends"""
    await initialize(ntfy_topic_url="https://ntfy.sh/test")
    
    # Send multiple notifications
    for i in range(15):
        try:
            await send_notification(f"Test {i}", priority="INFO")
        except Exception:
            pass  # Expected after limit
    
    # Verify rate limit status
    service = await get_service()
    status = await service.get_rate_limit_status()
    assert status["INFO"]["current"] >= 10
```

## Monitoring and Observability

### Metrics Tracking

```python
# Track notification metrics
import time
from notifications import send_notification, NotificationError

class NotificationMetrics:
    def __init__(self):
        self.sent = 0
        self.failed = 0
        self.rate_limited = 0
        self.total_time = 0
    
    async def send_tracked(self, message, **kwargs):
        """Send notification with metrics"""
        start = time.time()
        try:
            await send_notification(message, **kwargs)
            self.sent += 1
        except RateLimitExceededError:
            self.rate_limited += 1
        except NotificationError:
            self.failed += 1
        finally:
            self.total_time += time.time() - start
    
    def report(self):
        """Get metrics summary"""
        return {
            "sent": self.sent,
            "failed": self.failed,
            "rate_limited": self.rate_limited,
            "avg_time_ms": (self.total_time / max(self.sent, 1)) * 1000
        }

metrics = NotificationMetrics()
```

## Best Practices Summary

1. **Initialize once** at application startup
2. **Use appropriate priorities** (INFO for normal, ALERT for warnings, URGENT for critical)
3. **Add meaningful tags** for filtering/categorization
4. **Handle rate limits gracefully** (don't retry immediately)
5. **Monitor audit log** for troubleshooting
6. **Shutdown cleanly** in finally blocks
7. **Use global service pattern** to avoid passing service around
8. **Keep messages concise** but informative
9. **Test with real ntfy.sh** before production
10. **Monitor notification metrics** for system health

## Troubleshooting Integration Issues

### Notifications Not Being Sent

1. Check service is initialized: `if _service._initialized`
2. Verify ntfy topic URL: `await service.get_config("ntfy:topic_url")`
3. Check logs: `tail -f /var/log/sentient/notifications.log`
4. Test manually: `await send_notification("Test")`
5. Verify Redis is running: `redis-cli ping`

### Rate Limit Issues

1. Check status: `await service.get_rate_limit_status()`
2. Use URGENT priority for critical alerts
3. Space out INFO notifications
4. Check audit log for recent activity

### Integration Test Failures

1. Ensure Redis is running
2. Check test isolation (don't share state)
3. Mock ntfy.sh for unit tests
4. Use real ntfy.sh only for integration tests
5. Clean up audit log between tests

## License

Part of Sentient Core. See LICENSE file for details.
