# Notification Service - Complete Index

## Quick Navigation

### For Quick Start
1. **NOTIFICATIONS_QUICKREF.txt** - One-page cheat sheet with all essentials
2. **NOTIFICATIONS_README.md** - User guide with quick start section

### For Developers
1. **NOTIFICATIONS_API.md** - Complete API reference
2. **notifications.py** - Source code (well-documented)
3. **notifications_example.py** - 6 usage examples

### For Integration
1. **NOTIFICATIONS_INTEGRATION.md** - Integration patterns and examples
2. **notifications_example.py** - Production patterns

### For Testing
1. **test_notifications.py** - Unit test suite
2. **notifications_example.py** - Usage examples for manual testing

## File Structure

```
/opt/sentient-core/services/
├── Core Implementation
│   ├── notifications.py                  # Main service (577 lines)
│   ├── notifications_example.py          # Usage examples (250 lines)
│   ├── test_notifications.py             # Unit tests (447 lines)
│   └── requirements-notifications.txt   # Dependencies
│
├── Documentation (Primary)
│   ├── NOTIFICATIONS_README.md          # User guide (531 lines)
│   ├── NOTIFICATIONS_API.md             # API reference (625 lines)
│   └── NOTIFICATIONS_INTEGRATION.md     # Integration guide (505 lines)
│
└── Reference & Index
    ├── NOTIFICATIONS_SUMMARY.md         # Implementation summary
    ├── NOTIFICATIONS_QUICKREF.txt       # Quick reference card
    └── NOTIFICATIONS_INDEX.md           # This file
```

## What Each File Does

### notifications.py (Production Service)
**Purpose:** Main notification service implementation

**Contains:**
- NotificationService class (main implementation)
- PriorityLevel enum (INFO, ALERT, URGENT)
- RateLimitConfig class (rate limit configuration)
- Exception classes (NotificationError, RateLimitExceededError, NtfyShError)
- Global functions (initialize, send_notification, get_service)
- Signal handlers and main entry point

**Key Methods:**
- `initialize()` - Initialize Redis and HTTP
- `send_notification()` - Send notification via ntfy.sh
- `set_config()` / `get_config()` - Configuration management
- `get_rate_limit_status()` - Check rate limits
- `get_audit_log()` - Get notification history
- `shutdown()` - Graceful cleanup

**When to Read:**
- Implementation details
- Understanding the service architecture
- Extending or modifying the service

### notifications_example.py (Usage Examples)
**Purpose:** Demonstrate all usage patterns

**Contains 6 examples:**
1. Basic usage - Simple notifications with priorities
2. Rate limiting - Demonstrate rate limit behavior
3. Configuration - Set and retrieve configuration
4. Audit log - Retrieve and display notification history
5. Error handling - Handle all error types
6. Production pattern - Full application lifecycle

**When to Read:**
- Learning how to use the service
- Copy-paste examples for your code
- Understanding error handling patterns

### test_notifications.py (Unit Tests)
**Purpose:** Comprehensive test suite

**Contains:**
- TestNotificationService - Main service tests
- TestPriorityLevel - Enum tests
- TestRateLimitConfig - Configuration tests
- TestExceptionHierarchy - Exception tests
- Integration test helper

**When to Read/Run:**
- Verifying the service works
- Understanding test patterns
- Extending with your own tests
- Run: `pytest test_notifications.py -v`

### NOTIFICATIONS_README.md (User Guide)
**Purpose:** Complete user documentation

**Sections:**
- Features overview
- Quick start (5 minutes to working code)
- Installation instructions
- Basic API reference (simplified)
- ntfy.sh setup guide
- Rate limiting explanation
- Configuration management
- Production deployment
- Troubleshooting

**When to Read:**
- First introduction to the service
- Understanding features
- Getting started quickly
- Deploying to production

### NOTIFICATIONS_API.md (API Reference)
**Purpose:** Detailed API documentation

**Contains:**
- Class documentation with all methods
- Function signatures with parameters and returns
- Exception hierarchy with descriptions
- Configuration keys reference
- Redis key patterns
- Performance characteristics
- Thread safety information
- Complete examples for each method
- Best practices

**When to Read:**
- Looking up specific method signatures
- Understanding error handling
- Learning configuration options
- Understanding internals

### NOTIFICATIONS_INTEGRATION.md (Integration Guide)
**Purpose:** How to integrate with existing systems

**Contains:**
- MQTT event integration
- Wake word detection integration
- Error monitoring integration
- Health check integration
- Personality module integration
- API/web server integration
- Configuration workflow
- Systemd service setup
- Docker deployment
- Testing patterns
- Monitoring approaches

**When to Read:**
- Integrating with MQTT
- Setting up monitoring
- Deploying with systemd
- Creating Docker containers
- Adding to web API

### NOTIFICATIONS_SUMMARY.md (Overview)
**Purpose:** Implementation summary and checklist

**Contains:**
- Overview of entire system
- Component descriptions
- Architecture diagram
- Data flow explanation
- Key features list
- Quick start example
- All file descriptions
- Success criteria checklist

**When to Read:**
- High-level overview
- Understanding what was delivered
- Checking what features exist

### NOTIFICATIONS_QUICKREF.txt (Cheat Sheet)
**Purpose:** One-page reference for quick lookup

**Sections:**
- Installation
- Basic usage
- API functions and methods
- Priority levels
- Common examples
- Error handling
- Configuration
- Logging locations
- Redis keys
- Troubleshooting
- Best practices
- Integration patterns
- File locations

**When to Read:**
- Quick reference while coding
- Looking up command syntax
- Troubleshooting issues
- Quick integration reminder

## How to Use This Service

### Step 1: Installation
```bash
pip install -r /opt/sentient-core/services/requirements-notifications.txt
redis-server  # Start Redis
mkdir -p /var/log/sentient
```

### Step 2: Configuration
```python
from notifications import initialize

await initialize(ntfy_topic_url="https://ntfy.sh/my-sentient-core")
```

### Step 3: Send Notifications
```python
from notifications import send_notification

await send_notification("System ready", priority="INFO", title="Startup")
```

### Step 4: Handle Errors
```python
from notifications import RateLimitExceededError, NtfyShError

try:
    await send_notification("Message")
except RateLimitExceededError:
    logger.warning("Rate limited")
except NtfyShError:
    logger.error("ntfy.sh error")
```

## Reading Order

### For Users (Getting Started)
1. NOTIFICATIONS_QUICKREF.txt (2 min)
2. NOTIFICATIONS_README.md > Quick Start (5 min)
3. notifications_example.py (5 min)
4. Try running the example

### For Developers (Integration)
1. NOTIFICATIONS_SUMMARY.md (5 min)
2. NOTIFICATIONS_README.md (10 min)
3. NOTIFICATIONS_INTEGRATION.md (15 min)
4. Copy integration pattern and adapt

### For Architecture (Deep Dive)
1. NOTIFICATIONS_SUMMARY.md > Architecture (5 min)
2. NOTIFICATIONS_API.md (20 min)
3. notifications.py source code (20 min)
4. Run tests: `pytest test_notifications.py -v` (10 min)

### For Troubleshooting
1. NOTIFICATIONS_QUICKREF.txt > TROUBLESHOOTING (2 min)
2. NOTIFICATIONS_README.md > Troubleshooting (5 min)
3. Check logs: `tail -f /var/log/sentient/notifications.log`
4. Verify Redis: `redis-cli ping`

## Common Tasks

### "I want to send a notification"
→ NOTIFICATIONS_QUICKREF.txt > BASIC USAGE

### "How do I configure the service?"
→ NOTIFICATIONS_README.md > Configuration

### "What's the full API?"
→ NOTIFICATIONS_API.md

### "How do I integrate with MQTT?"
→ NOTIFICATIONS_INTEGRATION.md > MQTT Integration

### "It's not working, help!"
→ NOTIFICATIONS_README.md > Troubleshooting or NOTIFICATIONS_QUICKREF.txt > TROUBLESHOOTING

### "I want to see examples"
→ notifications_example.py (runnable) or NOTIFICATIONS_API.md (detailed)

### "I need to test my code"
→ test_notifications.py or notifications_example.py

### "I want to deploy to production"
→ NOTIFICATIONS_README.md > Production Deployment or NOTIFICATIONS_INTEGRATION.md > Deployment Integration

## Key Concepts

### Priority Levels
- **INFO**: 10/hour - Normal operations, low priority
- **ALERT**: 20/hour - Warnings and anomalies
- **URGENT**: Unlimited - Critical failures, must send

### Rate Limiting
- Per-priority hourly limits
- Rolling hour window
- Tracked in Redis
- Raises RateLimitExceededError when exceeded

### Configuration
- Stored in Redis
- Keys like `config:ntfy:topic_url`
- Set via `service.set_config(key, value)`
- Get via `service.get_config(key)`

### Audit Trail
- All sent notifications logged to Redis
- Last 1000 entries retained
- Includes timestamp, priority, title, message, tags
- Retrieved via `service.get_audit_log(limit=50)`

### Error Handling
- NotificationError (base)
  - RateLimitExceededError (rate limit exceeded)
  - NtfyShError (API/network error)

## Dependencies

### Required
- aiohttp >= 3.9.0 (HTTP client)
- redis >= 4.5.0 (Redis client)
- Python >= 3.9 (async/await)

### Optional
- pytest >= 7.0.0 (testing)
- pytest-asyncio >= 0.21.0 (async testing)

### External
- Redis server (localhost:6379)
- ntfy.sh (https://ntfy.sh)

## File Sizes

| File | Lines | Purpose |
|------|-------|---------|
| notifications.py | 577 | Main service |
| notifications_example.py | 250 | Usage examples |
| test_notifications.py | 447 | Unit tests |
| NOTIFICATIONS_README.md | 531 | User guide |
| NOTIFICATIONS_API.md | 625 | API reference |
| NOTIFICATIONS_INTEGRATION.md | 505 | Integration guide |
| NOTIFICATIONS_SUMMARY.md | 450+ | Overview |
| NOTIFICATIONS_QUICKREF.txt | 300+ | Quick ref |

**Total:** 3,686+ lines of code and documentation

## Support & Help

### Documentation
- User guide: NOTIFICATIONS_README.md
- API reference: NOTIFICATIONS_API.md
- Integration guide: NOTIFICATIONS_INTEGRATION.md
- Quick reference: NOTIFICATIONS_QUICKREF.txt

### Examples
- Basic usage: notifications_example.py
- Integration patterns: NOTIFICATIONS_INTEGRATION.md
- Complete API: NOTIFICATIONS_API.md

### Testing
- Unit tests: test_notifications.py
- Manual testing: notifications_example.py
- Run: `pytest test_notifications.py -v`

### Troubleshooting
- Quick fixes: NOTIFICATIONS_QUICKREF.txt
- Detailed help: NOTIFICATIONS_README.md
- Logs: `/var/log/sentient/notifications.log`

## Version & Status

- **Version:** 1.0 (Stable)
- **Status:** Production Ready
- **Created:** 2024-01-29
- **Python:** 3.9+
- **License:** Part of Sentient Core

## Next Steps

1. Read NOTIFICATIONS_QUICKREF.txt for overview (2 min)
2. Read NOTIFICATIONS_README.md for quick start (10 min)
3. Install dependencies (5 min)
4. Run notifications_example.py (5 min)
5. Integrate into your application (30 min)
6. Monitor with audit log and rate limit status (ongoing)

---

All files located in: `/opt/sentient-core/services/`
