#!/usr/bin/env python3
"""
Unit and integration tests for the Notification Service

Tests cover:
- Service initialization
- Configuration management
- Notification sending
- Rate limiting
- Error handling
- Audit logging
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from notifications import (
    NotificationService,
    PriorityLevel,
    RateLimitConfig,
    NotificationError,
    RateLimitExceededError,
    NtfyShError,
)

# Suppress logging during tests
logging.disable(logging.CRITICAL)


class TestNotificationService:
    """Test suite for NotificationService"""

    @pytest.fixture
    async def service(self):
        """Create a test service instance"""
        service = NotificationService(
            ntfy_topic_url="https://ntfy.sh/test-topic"
        )
        # Mock Redis and HTTP session
        service.redis_client = AsyncMock()
        service.http_session = AsyncMock()
        service._initialized = True
        service.running = True
        yield service
        await service.shutdown()

    @pytest.mark.asyncio
    async def test_initialization_success(self):
        """Test successful service initialization"""
        service = NotificationService(
            ntfy_topic_url="https://ntfy.sh/test-topic"
        )

        # Mock Redis and HTTP
        with patch('aioredis.from_url') as mock_redis:
            with patch('aiohttp.ClientSession') as mock_session:
                mock_redis_instance = AsyncMock()
                mock_redis_instance.ping = AsyncMock(return_value=True)
                mock_redis.return_value = mock_redis_instance

                mock_session_instance = AsyncMock()
                mock_session.return_value = mock_session_instance

                result = await service.initialize()

                assert result is True
                assert service._initialized is True
                mock_redis_instance.ping.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialization_redis_failure(self):
        """Test initialization when Redis fails"""
        service = NotificationService()

        with patch('aioredis.from_url') as mock_redis:
            mock_redis.side_effect = Exception("Redis connection failed")

            result = await service.initialize()

            assert result is False
            assert service._initialized is False

    @pytest.mark.asyncio
    async def test_send_notification_success(self, service):
        """Test successful notification sending"""
        # Mock HTTP response
        mock_response = AsyncMock()
        mock_response.status = 200
        service.http_session.post = AsyncMock(
            return_value=mock_response.__aenter__.return_value
        )
        mock_response.__aenter__.return_value = mock_response

        # Mock Redis increment
        service.redis_client.incr = AsyncMock(return_value=1)
        service.redis_client.expire = AsyncMock(return_value=True)
        service.redis_client.lpush = AsyncMock(return_value=True)
        service.redis_client.ltrim = AsyncMock(return_value=True)

        result = await service.send_notification(
            message="Test message",
            priority="INFO",
            title="Test",
        )

        assert result is True
        service.http_session.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_notification_empty_message(self, service):
        """Test notification with empty message fails"""
        with pytest.raises(NotificationError):
            await service.send_notification(message="")

    @pytest.mark.asyncio
    async def test_send_notification_invalid_priority(self, service):
        """Test notification with invalid priority fails"""
        with pytest.raises(NotificationError):
            await service.send_notification(
                message="Test",
                priority="INVALID"
            )

    @pytest.mark.asyncio
    async def test_rate_limit_info(self, service):
        """Test rate limiting for INFO priority"""
        # Configure limit to 3 for testing
        RateLimitConfig.LIMITS[PriorityLevel.INFO] = 3

        # Mock Redis
        service.redis_client.incr = AsyncMock(side_effect=[1, 2, 3, 4])
        service.redis_client.expire = AsyncMock(return_value=True)
        service.redis_client.get = AsyncMock(return_value=None)
        service.redis_client.lpush = AsyncMock(return_value=True)
        service.redis_client.ltrim = AsyncMock(return_value=True)

        # Mock HTTP
        mock_response = AsyncMock()
        mock_response.status = 200
        service.http_session.post = AsyncMock(
            return_value=mock_response.__aenter__.return_value
        )
        mock_response.__aenter__.return_value = mock_response

        # First 3 should succeed
        for i in range(3):
            result = await service.send_notification(
                message=f"Test {i}",
                priority="INFO"
            )
            assert result is True

        # 4th should fail with rate limit
        with pytest.raises(RateLimitExceededError):
            await service.send_notification(
                message="Test 4",
                priority="INFO"
            )

        # Restore original limit
        RateLimitConfig.LIMITS[PriorityLevel.INFO] = 10

    @pytest.mark.asyncio
    async def test_rate_limit_urgent_unlimited(self, service):
        """Test URGENT priority has no rate limit"""
        service.redis_client.incr = AsyncMock(side_effect=range(1, 100))
        service.redis_client.expire = AsyncMock(return_value=True)
        service.redis_client.lpush = AsyncMock(return_value=True)
        service.redis_client.ltrim = AsyncMock(return_value=True)

        # Mock HTTP
        mock_response = AsyncMock()
        mock_response.status = 200
        service.http_session.post = AsyncMock(
            return_value=mock_response.__aenter__.return_value
        )
        mock_response.__aenter__.return_value = mock_response

        # Send many URGENT notifications - all should succeed
        for i in range(20):
            result = await service.send_notification(
                message=f"Urgent {i}",
                priority="URGENT"
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_set_config(self, service):
        """Test configuration setting"""
        service.redis_client.set = AsyncMock(return_value=True)

        result = await service.set_config("test:key", "test_value")

        assert result is True
        service.redis_client.set.assert_called_once_with(
            "config:test:key",
            "test_value"
        )

    @pytest.mark.asyncio
    async def test_get_config(self, service):
        """Test configuration retrieval"""
        service.redis_client.get = AsyncMock(return_value="test_value")

        result = await service.get_config("test:key")

        assert result == "test_value"
        service.redis_client.get.assert_called_once_with("config:test:key")

    @pytest.mark.asyncio
    async def test_get_rate_limit_status(self, service):
        """Test rate limit status retrieval"""
        service.redis_client.get = AsyncMock(
            side_effect=["5", "3", "2"]
        )

        status = await service.get_rate_limit_status()

        assert "INFO" in status
        assert "ALERT" in status
        assert "URGENT" in status
        assert status["INFO"]["current"] == 5
        assert status["ALERT"]["current"] == 3
        assert status["URGENT"]["current"] == 2

    @pytest.mark.asyncio
    async def test_get_audit_log(self, service):
        """Test audit log retrieval"""
        entries = [
            json.dumps({
                "timestamp": "2024-01-29T10:00:00",
                "priority": "INFO",
                "title": "Test 1",
                "message": "Message 1",
            }),
            json.dumps({
                "timestamp": "2024-01-29T10:01:00",
                "priority": "ALERT",
                "title": "Test 2",
                "message": "Message 2",
            }),
        ]

        service.redis_client.lrange = AsyncMock(return_value=entries)

        log = await service.get_audit_log(limit=10)

        assert len(log) == 2
        assert log[0]["title"] == "Test 1"
        assert log[1]["title"] == "Test 2"
        service.redis_client.lrange.assert_called_once()

    @pytest.mark.asyncio
    async def test_ntfysh_api_error(self, service):
        """Test ntfy.sh API error handling"""
        # Mock HTTP error response
        mock_response = AsyncMock()
        mock_response.status = 400
        mock_response.text = AsyncMock(return_value="Bad request")
        service.http_session.post = AsyncMock(
            return_value=mock_response.__aenter__.return_value
        )
        mock_response.__aenter__.return_value = mock_response

        # Mock Redis for rate limit
        service.redis_client.incr = AsyncMock(return_value=1)
        service.redis_client.expire = AsyncMock(return_value=True)

        with pytest.raises(NtfyShError):
            await service.send_notification(message="Test")

    @pytest.mark.asyncio
    async def test_ntfysh_timeout(self, service):
        """Test ntfy.sh timeout handling"""
        service.http_session.post = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )

        # Mock Redis for rate limit
        service.redis_client.incr = AsyncMock(return_value=1)
        service.redis_client.expire = AsyncMock(return_value=True)

        with pytest.raises(NtfyShError):
            await service.send_notification(message="Test")

    @pytest.mark.asyncio
    async def test_notification_with_tags(self, service):
        """Test notification with tags"""
        # Mock HTTP
        mock_response = AsyncMock()
        mock_response.status = 200
        service.http_session.post = AsyncMock(
            return_value=mock_response.__aenter__.return_value
        )
        mock_response.__aenter__.return_value = mock_response

        # Mock Redis
        service.redis_client.incr = AsyncMock(return_value=1)
        service.redis_client.expire = AsyncMock(return_value=True)
        service.redis_client.lpush = AsyncMock(return_value=True)
        service.redis_client.ltrim = AsyncMock(return_value=True)

        result = await service.send_notification(
            message="Test",
            priority="INFO",
            title="Test",
            tags=["tag1", "tag2"],
        )

        assert result is True

        # Verify tags were included in request
        call_args = service.http_session.post.call_args
        headers = call_args.kwargs["headers"]
        assert "Tags" in headers
        assert "tag1" in headers["Tags"]

    @pytest.mark.asyncio
    async def test_uninitialized_service(self):
        """Test that uninitialized service raises error"""
        service = NotificationService()
        service._initialized = False
        service.running = False

        with pytest.raises(NotificationError):
            await service.send_notification(message="Test")

    @pytest.mark.asyncio
    async def test_priority_enum_values(self):
        """Test priority enum values match ntfy.sh spec"""
        assert PriorityLevel.INFO.value == 2
        assert PriorityLevel.ALERT.value == 4
        assert PriorityLevel.URGENT.value == 5

    @pytest.mark.asyncio
    async def test_shutdown_closes_connections(self, service):
        """Test that shutdown closes connections properly"""
        service.http_session.close = AsyncMock()
        service.redis_client.close = AsyncMock()

        await service.shutdown()

        assert service.running is False
        service.http_session.close.assert_called_once()
        service.redis_client.close.assert_called_once()


# Standalone tests (not using fixtures)
class TestPriorityLevel:
    """Test priority level enum"""

    def test_priority_enum_members(self):
        """Test all priority levels are defined"""
        assert hasattr(PriorityLevel, 'INFO')
        assert hasattr(PriorityLevel, 'ALERT')
        assert hasattr(PriorityLevel, 'URGENT')

    def test_priority_enum_access(self):
        """Test accessing priority levels"""
        assert PriorityLevel['INFO'] == PriorityLevel.INFO
        assert PriorityLevel['ALERT'] == PriorityLevel.ALERT
        assert PriorityLevel['URGENT'] == PriorityLevel.URGENT


class TestRateLimitConfig:
    """Test rate limit configuration"""

    def test_rate_limit_config_exists(self):
        """Test rate limit config is defined"""
        assert hasattr(RateLimitConfig, 'LIMITS')
        assert hasattr(RateLimitConfig, 'TIME_WINDOW')

    def test_rate_limit_window(self):
        """Test rate limit window is 1 hour"""
        assert RateLimitConfig.TIME_WINDOW == 3600


class TestExceptionHierarchy:
    """Test exception class hierarchy"""

    def test_exception_inheritance(self):
        """Test exception class relationships"""
        assert issubclass(RateLimitExceededError, NotificationError)
        assert issubclass(NtfyShError, NotificationError)

    def test_exception_instantiation(self):
        """Test exceptions can be created and raised"""
        with pytest.raises(NotificationError):
            raise RateLimitExceededError("Test")

        with pytest.raises(NotificationError):
            raise NtfyShError("Test")


# Integration test helper
async def integration_test_notification_flow():
    """
    Integration test: full notification flow
    Run this with real Redis and optionally real ntfy.sh

    To run:
        python3 -c "import asyncio; from test_notifications import integration_test_notification_flow; asyncio.run(integration_test_notification_flow())"
    """
    from notifications import initialize, send_notification, get_service

    try:
        # Initialize
        service = await initialize(
            ntfy_topic_url="https://ntfy.sh/sentient-test"
        )

        print("✓ Service initialized")

        # Test configuration
        await service.set_config("test:key", "test_value")
        value = await service.get_config("test:key")
        assert value == "test_value"
        print("✓ Configuration working")

        # Test rate limiting status
        status = await service.get_rate_limit_status()
        assert "INFO" in status
        print(f"✓ Rate limit status: {status}")

        # Test audit log
        log = await service.get_audit_log(limit=5)
        print(f"✓ Audit log retrieved ({len(log)} entries)")

        # Shutdown
        await service.shutdown()
        print("✓ Service shutdown")

    except Exception as e:
        print(f"✗ Integration test failed: {e}")
        raise


if __name__ == "__main__":
    # Run pytest
    pytest.main([__file__, "-v"])

    # Optionally run integration test
    # asyncio.run(integration_test_notification_flow())
