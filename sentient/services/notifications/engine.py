#!/usr/bin/env python3
"""
Notification Service for Sentient Core
Sends notifications via ntfy.sh with rate limiting and priority levels.
Production-ready with full async/await support, Redis integration, and error handling.
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any

import aiohttp
import redis.asyncio as aioredis

from sentient.config import get_config
from sentient.common.logging import setup_logging


class PriorityLevel(Enum):
    """Notification priority levels"""
    INFO = 2
    ALERT = 4
    URGENT = 5


class RateLimitConfig:
    """Rate limiting configuration per priority level"""

    # Max notifications per hour per priority
    LIMITS = {
        PriorityLevel.INFO: 10,
        PriorityLevel.ALERT: 20,
        PriorityLevel.URGENT: None,  # Unlimited
    }

    # Time window in seconds
    TIME_WINDOW = 3600  # 1 hour


class NotificationError(Exception):
    """Base exception for notification service"""
    pass


class RateLimitExceededError(NotificationError):
    """Raised when rate limit is exceeded"""
    pass


class NtfyShError(NotificationError):
    """Raised when ntfy.sh API returns an error"""
    pass


class NotificationService:
    """Production-ready notification service with ntfy.sh integration"""

    def __init__(self, ntfy_topic_url: Optional[str] = None):
        """
        Initialize notification service.

        Args:
            ntfy_topic_url: Full URL for ntfy.sh topic (e.g., https://ntfy.sh/my-sentient-core)
                           If None, will load from config
        """
        self.config = get_config()
        self.logger = setup_logging("notifications")

        # Use config values
        if ntfy_topic_url:
            self.ntfy_topic_url = ntfy_topic_url
        else:
            # Build from config
            if self.config.ntfy.topic:
                self.ntfy_topic_url = f"{self.config.ntfy.server}/{self.config.ntfy.topic}"
            else:
                self.ntfy_topic_url = None

        self.redis_prefix = "notifications:ratelimit:"
        self.redis_client: Optional[aioredis.Redis] = None
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.running = False
        self._initialized = False

    async def initialize(self) -> bool:
        """
        Initialize Redis and HTTP connections.

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Initialize Redis connection
            self.redis_client = await aioredis.from_url(
                f"redis://{self.config.redis.host}:{self.config.redis.port}/{self.config.redis.db}",
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True,
            )

            # Test Redis connection
            await self.redis_client.ping()
            self.logger.info("Redis connection established")

            # Initialize HTTP session with proper configuration
            self.http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10, connect=5),
                connector=aiohttp.TCPConnector(
                    ssl=True,
                    limit=10,
                    limit_per_host=5,
                )
            )
            self.logger.info("HTTP session initialized")

            # Warn if no ntfy topic URL configured
            if not self.ntfy_topic_url:
                self.logger.warning("No ntfy topic URL configured. Set via config or set_config()")

            self._initialized = True
            self.running = True
            self.logger.info("Notification service initialized successfully")
            return True

        except aioredis.ConnectionError as e:
            self.logger.error(f"Failed to connect to Redis: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Initialization error: {e}", exc_info=True)
            return False

    async def set_config(self, key: str, value: str) -> bool:
        """
        Set configuration in Redis.

        Args:
            key: Configuration key (e.g., 'ntfy:topic_url')
            value: Configuration value

        Returns:
            bool: True if successful
        """
        if not self.redis_client:
            self.logger.error("Redis client not initialized")
            return False

        try:
            redis_key = f"config:{key}"
            await self.redis_client.set(redis_key, value)

            # Update local config if setting topic URL
            if key == "ntfy:topic_url":
                self.ntfy_topic_url = value

            self.logger.info(f"Configuration updated: {key}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to set config {key}: {e}")
            return False

    async def get_config(self, key: str) -> Optional[str]:
        """Get configuration from Redis"""
        if not self.redis_client:
            return None

        try:
            redis_key = f"config:{key}"
            return await self.redis_client.get(redis_key)
        except Exception as e:
            self.logger.error(f"Failed to get config {key}: {e}")
            return None

    async def _check_rate_limit(self, priority: PriorityLevel) -> bool:
        """
        Check if notification is within rate limits.

        Args:
            priority: Priority level of notification

        Returns:
            bool: True if within limits, False if exceeded
        """
        if not self.redis_client:
            self.logger.warning("Rate limiting disabled: Redis not available")
            return True

        limit = RateLimitConfig.LIMITS.get(priority)

        # URGENT has no rate limit
        if limit is None:
            return True

        try:
            # Create rate limit key
            now = datetime.now(timezone.utc)
            hour_key = now.strftime("%Y%m%d%H")
            rate_key = f"{self.redis_prefix}{priority.name}:{hour_key}"

            # Get current count
            current = await self.redis_client.incr(rate_key)

            # Set expiration on first increment
            if current == 1:
                await self.redis_client.expire(rate_key, RateLimitConfig.TIME_WINDOW)

            # Check limit
            if current > limit:
                self.logger.warning(
                    f"Rate limit exceeded for {priority.name}: "
                    f"{current}/{limit} in current hour"
                )
                return False

            self.logger.debug(f"Rate limit OK for {priority.name}: {current}/{limit}")
            return True

        except Exception as e:
            self.logger.error(f"Rate limit check error: {e}")
            # Fail open - allow notification if Redis fails
            return True

    async def _send_to_ntfysh(
        self,
        message: str,
        title: Optional[str] = None,
        priority: PriorityLevel = PriorityLevel.INFO,
        tags: Optional[list] = None,
    ) -> bool:
        """
        Send notification to ntfy.sh API.

        Args:
            message: Notification message body
            title: Optional notification title
            priority: Priority level
            tags: Optional list of tags

        Returns:
            bool: True if sent successfully

        Raises:
            NtfyShError: If API returns an error
        """
        if not self.http_session or not self.ntfy_topic_url:
            raise NtfyShError("HTTP session or ntfy topic URL not initialized")

        try:
            # Build headers according to ntfy.sh documentation
            headers = {
                "Title": title or "Sentient Core Notification",
                "Priority": str(priority.value),
                "Content-Type": "text/plain; charset=utf-8",
            }

            # Add tags if provided
            if tags:
                headers["Tags"] = ",".join(tags)

            # Send POST request to ntfy.sh
            async with self.http_session.post(
                self.ntfy_topic_url,
                data=message.encode('utf-8'),
                headers=headers,
                ssl=True,
            ) as response:
                # Check response status
                if response.status == 200:
                    self.logger.info(
                        f"Notification sent successfully via ntfy.sh "
                        f"(priority={priority.name}, title={title})"
                    )
                    return True
                elif response.status >= 400:
                    error_text = await response.text()
                    raise NtfyShError(
                        f"ntfy.sh API error {response.status}: {error_text}"
                    )
                else:
                    raise NtfyShError(f"Unexpected ntfy.sh response: {response.status}")

        except aiohttp.ClientError as e:
            raise NtfyShError(f"HTTP request failed: {e}")
        except asyncio.TimeoutError:
            raise NtfyShError("ntfy.sh request timeout")

    async def send_notification(
        self,
        message: str,
        priority: str = "INFO",
        title: Optional[str] = None,
        tags: Optional[list] = None,
    ) -> bool:
        """
        Send a notification via ntfy.sh.

        Args:
            message: Notification message body (required)
            priority: Priority level - 'INFO', 'ALERT', or 'URGENT' (default: 'INFO')
            title: Optional notification title
            tags: Optional list of tags for categorization

        Returns:
            bool: True if sent successfully

        Raises:
            NotificationError: If sending fails (includes RateLimitExceededError, NtfyShError)
        """
        if not self._initialized or not self.running:
            raise NotificationError("Notification service not initialized")

        if not message or not message.strip():
            raise NotificationError("Message cannot be empty")

        # Parse priority level
        try:
            priority_enum = PriorityLevel[priority.upper()]
        except KeyError:
            raise NotificationError(
                f"Invalid priority '{priority}'. Must be INFO, ALERT, or URGENT"
            )

        # Check rate limits
        if not await self._check_rate_limit(priority_enum):
            raise RateLimitExceededError(
                f"Rate limit exceeded for {priority} notifications"
            )

        # Send notification
        try:
            success = await self._send_to_ntfysh(
                message=message.strip(),
                title=title,
                priority=priority_enum,
                tags=tags,
            )

            # Log to Redis for audit trail
            if self.redis_client:
                try:
                    audit_entry = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "priority": priority_enum.name,
                        "title": title,
                        "message": message[:100],  # Store first 100 chars
                        "status": "sent",
                        "tags": tags or [],
                    }
                    await self.redis_client.lpush(
                        "notifications:audit_log",
                        json.dumps(audit_entry)
                    )
                    # Keep last 1000 notifications in audit log
                    await self.redis_client.ltrim("notifications:audit_log", 0, 999)
                except Exception as e:
                    self.logger.warning(f"Failed to log to audit trail: {e}")

            return success

        except NtfyShError as e:
            self.logger.error(f"Failed to send notification: {e}")
            raise

    async def get_rate_limit_status(self) -> Dict[str, Any]:
        """
        Get current rate limit status for all priority levels.

        Returns:
            dict: Rate limit status per priority level
        """
        if not self.redis_client:
            return {"error": "Redis not available"}

        try:
            now = datetime.now(timezone.utc)
            hour_key = now.strftime("%Y%m%d%H")
            status = {}

            for priority in PriorityLevel:
                limit = RateLimitConfig.LIMITS.get(priority)
                rate_key = f"{self.redis_prefix}{priority.name}:{hour_key}"

                try:
                    current = await self.redis_client.get(rate_key)
                    current = int(current) if current else 0
                except Exception:
                    current = 0

                status[priority.name] = {
                    "current": current,
                    "limit": limit,
                    "unlimited": limit is None,
                }

            return status
        except Exception as e:
            self.logger.error(f"Failed to get rate limit status: {e}")
            return {"error": str(e)}

    async def get_audit_log(self, limit: int = 50) -> list:
        """
        Get recent notifications from audit log.

        Args:
            limit: Maximum number of entries to return

        Returns:
            list: Recent notification entries
        """
        if not self.redis_client:
            return []

        try:
            entries = await self.redis_client.lrange(
                "notifications:audit_log",
                0,
                limit - 1
            )
            return [json.loads(entry) for entry in entries]
        except Exception as e:
            self.logger.error(f"Failed to get audit log: {e}")
            return []

    async def shutdown(self):
        """Gracefully shutdown the service"""
        self.logger.info("Shutting down notification service...")

        self.running = False

        # Close HTTP session
        if self.http_session:
            await self.http_session.close()
            self.logger.info("HTTP session closed")

        # Close Redis connection
        if self.redis_client:
            await self.redis_client.close()
            self.logger.info("Redis connection closed")

        self._initialized = False
        self.logger.info("Notification service shutdown complete")


# Global service instance
_service: Optional[NotificationService] = None


async def initialize(ntfy_topic_url: Optional[str] = None) -> NotificationService:
    """
    Initialize global notification service.

    Args:
        ntfy_topic_url: Optional ntfy.sh topic URL

    Returns:
        NotificationService: Initialized service instance
    """
    global _service

    _service = NotificationService(ntfy_topic_url=ntfy_topic_url)
    if not await _service.initialize():
        raise RuntimeError("Failed to initialize notification service")

    return _service


async def send_notification(
    message: str,
    priority: str = "INFO",
    title: Optional[str] = None,
    tags: Optional[list] = None,
) -> bool:
    """
    Send a notification using the global service.

    Args:
        message: Notification message
        priority: Priority level ('INFO', 'ALERT', 'URGENT')
        title: Optional title
        tags: Optional list of tags

    Returns:
        bool: True if successful
    """
    if _service is None:
        raise RuntimeError("Notification service not initialized. Call initialize() first.")

    return await _service.send_notification(
        message=message,
        priority=priority,
        title=title,
        tags=tags,
    )


async def get_service() -> NotificationService:
    """Get the global notification service instance"""
    if _service is None:
        raise RuntimeError("Notification service not initialized")
    return _service


if __name__ == "__main__":
    import signal

    async def main():
        """
        Example/test service runner.
        In production, import and use initialize() and send_notification() instead.
        """
        global _service

        # Create log directory
        Path('/var/log/sentient').mkdir(parents=True, exist_ok=True)

        def signal_handler(signum, frame):
            """Handle shutdown signals"""
            print(f"Received signal {signum}, shutting down...")
            if _service:
                asyncio.create_task(_service.shutdown())
            sys.exit(0)

        # Set up signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        try:
            # Initialize service
            print("Initializing notification service...")
            _service = NotificationService()

            if not await _service.initialize():
                print("Failed to initialize service")
                return

            print("Notification service ready")
            print("Set ntfy topic URL with: service.set_config('ntfy:topic_url', 'https://ntfy.sh/your-topic')")
            print("Send notification with: await service.send_notification('Test message')")

            # Keep service running
            while _service.running:
                await asyncio.sleep(1)

        except Exception as e:
            print(f"Service error: {e}")

        finally:
            if _service:
                await _service.shutdown()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Service terminated by user")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)
