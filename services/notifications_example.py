#!/usr/bin/env python3
"""
Example usage of the Notification Service

Demonstrates:
- Initializing the service
- Configuring ntfy.sh topic URL
- Sending notifications with different priorities
- Rate limit handling
- Audit log retrieval
- Rate limit status checking
"""

import asyncio
from notifications import (
    initialize,
    send_notification,
    get_service,
    NotificationError,
    RateLimitExceededError,
)


async def example_basic_usage():
    """Basic usage example"""
    print("=== BASIC USAGE ===\n")

    # Initialize service with ntfy.sh topic URL
    service = await initialize(ntfy_topic_url="https://ntfy.sh/sentient-core-demo")

    try:
        # Send a simple INFO notification
        await send_notification(
            message="System initialized successfully",
            priority="INFO",
            title="Startup",
        )
        print("✓ INFO notification sent")

        # Send an ALERT
        await send_notification(
            message="CPU usage is above 80%",
            priority="ALERT",
            title="Performance Warning",
            tags=["performance", "warning"],
        )
        print("✓ ALERT notification sent")

        # Send an URGENT notification
        await send_notification(
            message="Critical system failure detected",
            priority="URGENT",
            title="SYSTEM CRITICAL",
            tags=["critical", "error"],
        )
        print("✓ URGENT notification sent")

    except NotificationError as e:
        print(f"✗ Error: {e}")

    await service.shutdown()


async def example_rate_limiting():
    """Demonstrate rate limiting"""
    print("\n=== RATE LIMITING ===\n")

    service = await initialize(ntfy_topic_url="https://ntfy.sh/sentient-core-demo")

    try:
        # Try to send 15 INFO notifications (limit is 10/hour)
        for i in range(15):
            try:
                await send_notification(
                    message=f"Test message {i+1}",
                    priority="INFO",
                    title=f"Test {i+1}",
                )
                print(f"✓ Notification {i+1} sent")
            except RateLimitExceededError:
                print(f"✗ Notification {i+1} blocked by rate limit")
            except NotificationError as e:
                print(f"✗ Notification {i+1} error: {e}")

        # Check rate limit status
        status = await service.get_rate_limit_status()
        print(f"\nRate limit status: {status}")

    finally:
        await service.shutdown()


async def example_config_management():
    """Demonstrate configuration management"""
    print("\n=== CONFIGURATION MANAGEMENT ===\n")

    service = await initialize()

    try:
        # Set ntfy.sh topic URL via config
        success = await service.set_config(
            "ntfy:topic_url",
            "https://ntfy.sh/my-awesome-sentient-core"
        )
        print(f"Set config: {success}")

        # Retrieve configuration
        topic_url = await service.get_config("ntfy:topic_url")
        print(f"Ntfy topic URL: {topic_url}")

        # Set custom configuration
        await service.set_config("app:name", "Cortana")
        app_name = await service.get_config("app:name")
        print(f"App name: {app_name}")

    finally:
        await service.shutdown()


async def example_audit_log():
    """Demonstrate audit log retrieval"""
    print("\n=== AUDIT LOG ===\n")

    service = await initialize(ntfy_topic_url="https://ntfy.sh/sentient-core-demo")

    try:
        # Send some notifications
        for i in range(3):
            await send_notification(
                message=f"Test audit message {i+1}",
                priority="INFO",
                title=f"Audit Test {i+1}",
            )

        # Get audit log
        log = await service.get_audit_log(limit=10)
        print(f"Last {len(log)} notifications:\n")

        for entry in log:
            print(f"  {entry['timestamp']}")
            print(f"    Priority: {entry['priority']}")
            print(f"    Title: {entry['title']}")
            print(f"    Message: {entry['message']}")
            print()

    finally:
        await service.shutdown()


async def example_error_handling():
    """Demonstrate error handling"""
    print("\n=== ERROR HANDLING ===\n")

    service = await initialize(ntfy_topic_url="https://ntfy.sh/sentient-core-demo")

    try:
        # Try invalid priority
        try:
            await send_notification(
                message="Test",
                priority="INVALID"
            )
        except NotificationError as e:
            print(f"✓ Caught invalid priority: {e}")

        # Try empty message
        try:
            await send_notification(
                message="",
                priority="INFO"
            )
        except NotificationError as e:
            print(f"✓ Caught empty message: {e}")

        # Try without initialized service (would fail)
        # This is handled by the global service pattern

        # Try with tags
        await send_notification(
            message="Message with tags",
            priority="INFO",
            title="Tags Test",
            tags=["sensor", "temperature", "alert"],
        )
        print("✓ Notification with tags sent")

    except Exception as e:
        print(f"✗ Unexpected error: {e}")

    finally:
        await service.shutdown()


async def example_production_pattern():
    """Production usage pattern"""
    print("\n=== PRODUCTION PATTERN ===\n")

    # Initialize once at application startup
    service = await initialize(ntfy_topic_url="https://ntfy.sh/sentient-core-prod")

    try:
        # Use throughout application lifetime
        await send_notification(
            message="Application started",
            priority="INFO",
            title="Startup",
        )

        # Simulate some work with notifications
        for i in range(3):
            await asyncio.sleep(1)
            await send_notification(
                message=f"Processing step {i+1}",
                priority="INFO",
                title="Processing",
            )

        # Before shutdown, get statistics
        status = await service.get_rate_limit_status()
        log = await service.get_audit_log(limit=5)

        print(f"\nFinal statistics:")
        print(f"  Rate limits: {status}")
        print(f"  Recent notifications: {len(log)}")

    finally:
        # Shutdown cleanly
        await service.shutdown()
        print("\n✓ Application shutdown complete")


async def main():
    """Run examples"""
    print("Notification Service Examples\n")
    print("=" * 50)

    # Run examples
    await example_basic_usage()
    await example_rate_limiting()
    await example_config_management()
    await example_audit_log()
    await example_error_handling()
    await example_production_pattern()

    print("\n" + "=" * 50)
    print("\nAll examples completed!")


if __name__ == "__main__":
    asyncio.run(main())
