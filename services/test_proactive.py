#!/usr/bin/env python3
"""
Test Script for Proactive Behavior Engine

Tests all trigger types and delivery mechanisms.
"""

import asyncio
import json
import time
from datetime import datetime
import redis.asyncio as redis
import aiomqtt


async def setup_redis():
    """Connect to Redis"""
    client = await redis.from_url("redis://localhost:6379", decode_responses=True)
    await client.ping()
    print("✓ Connected to Redis")
    return client


async def setup_mqtt():
    """Connect to MQTT"""
    client = aiomqtt.Client(hostname="localhost", port=1883)
    print("✓ Connected to MQTT")
    return client


async def test_boredom_trigger(redis_client, mqtt_client):
    """Test BOREDOM trigger"""
    print("\n=== Testing BOREDOM Trigger ===")

    # Set last interaction to 31 minutes ago
    timestamp = time.time() - (31 * 60)
    await redis_client.set("interaction:last_timestamp", str(timestamp))
    print(f"Set last interaction to 31 minutes ago: {timestamp}")

    # Clear cooldown
    await redis_client.delete("proactive:last_activation:boredom")
    print("Cleared BOREDOM cooldown")

    # Publish world state with Jack present
    world_state = {
        "timestamp": datetime.now().isoformat(),
        "jack_present": True,
        "threat_level": 0,
        "ambient_state": "quiet",
        "system_health": {}
    }

    async with mqtt_client as client:
        await client.publish(
            "sentient/world/state",
            json.dumps(world_state).encode()
        )
    print("Published world state with jack_present=True")

    print("\n⏳ Waiting up to 90 seconds for BOREDOM trigger activation...")
    print("   Watch logs: sudo journalctl -u sentient-proactive.service -f")


async def test_concern_trigger(redis_client, mqtt_client):
    """Test CONCERN trigger"""
    print("\n=== Testing CONCERN Trigger ===")

    # Clear cooldown
    await redis_client.delete("proactive:last_activation:concern")
    print("Cleared CONCERN cooldown")

    # Publish world state with high threat level
    world_state = {
        "timestamp": datetime.now().isoformat(),
        "jack_present": True,
        "threat_level": 7,
        "active_threats": [
            {
                "type": "rf_anomaly",
                "severity": 7,
                "source": "ESP32",
                "timestamp": datetime.now().isoformat()
            }
        ],
        "ambient_state": "active",
        "system_health": {}
    }

    async with mqtt_client as client:
        await client.publish(
            "sentient/world/state",
            json.dumps(world_state).encode()
        )
    print("Published world state with threat_level=7")

    print("\n⏳ Waiting up to 60 seconds for CONCERN trigger activation...")
    print("   Watch logs: sudo journalctl -u sentient-proactive.service -f")


async def test_curiosity_trigger(redis_client, mqtt_client):
    """Test CURIOSITY trigger"""
    print("\n=== Testing CURIOSITY Trigger ===")

    # Clear cooldown
    await redis_client.delete("proactive:last_activation:curiosity")
    print("Cleared CURIOSITY cooldown")

    # Set sensor anomaly count
    await redis_client.set("sensor:anomaly_count", "3")
    print("Set sensor:anomaly_count=3")

    # Publish world state with interesting ambient state
    world_state = {
        "timestamp": datetime.now().isoformat(),
        "jack_present": True,
        "threat_level": 0,
        "ambient_state": "active",  # Changed from quiet
        "system_health": {}
    }

    async with mqtt_client as client:
        await client.publish(
            "sentient/world/state",
            json.dumps(world_state).encode()
        )
    print("Published world state with ambient_state='active'")

    print("\n⏳ Waiting up to 150 seconds for CURIOSITY trigger activation...")
    print("   Watch logs: sudo journalctl -u sentient-proactive.service -f")


async def test_care_trigger(redis_client, mqtt_client):
    """Test CARE trigger"""
    print("\n=== Testing CARE Trigger ===")

    # Clear cooldown
    await redis_client.delete("proactive:last_activation:care")
    print("Cleared CARE cooldown")

    # Set last interaction to 8 hours ago (overnight)
    timestamp = time.time() - (8 * 3600)
    await redis_client.set("interaction:last_timestamp", str(timestamp))
    print(f"Set last interaction to 8 hours ago: {timestamp}")

    current_hour = datetime.now().hour
    print(f"Current hour: {current_hour}")

    if 8 <= current_hour <= 10:
        print("✓ Within morning window (8-10am) - CARE trigger should activate")
    elif current_hour >= 23 or current_hour < 6:
        print("✓ Within late night window - CARE trigger should activate")
    else:
        print("⚠ Not in optimal time window - CARE trigger may not activate")
        print("  Optimal windows: 8-10am OR 11pm-6am")

    # Publish world state
    world_state = {
        "timestamp": datetime.now().isoformat(),
        "jack_present": True,
        "threat_level": 0,
        "ambient_state": "quiet",
        "time_context": "morning" if 8 <= current_hour <= 10 else "night",
        "system_health": {}
    }

    async with mqtt_client as client:
        await client.publish(
            "sentient/world/state",
            json.dumps(world_state).encode()
        )
    print("Published world state")

    print("\n⏳ Waiting up to 330 seconds for CARE trigger activation...")
    print("   Watch logs: sudo journalctl -u sentient-proactive.service -f")


async def test_excitement_trigger(redis_client, mqtt_client):
    """Test EXCITEMENT trigger"""
    print("\n=== Testing EXCITEMENT Trigger ===")

    # Clear cooldown
    await redis_client.delete("proactive:last_activation:excitement")
    print("Cleared EXCITEMENT cooldown")

    # Set system achievement (recent)
    achievement = {
        "timestamp": time.time(),
        "importance": 0.85,
        "description": "Optimized vision pipeline - 35% faster!",
        "type": "performance"
    }
    await redis_client.set("system:latest_achievement", json.dumps(achievement))
    print(f"Set system:latest_achievement: {achievement['description']}")

    # Publish world state
    world_state = {
        "timestamp": datetime.now().isoformat(),
        "jack_present": True,
        "threat_level": 0,
        "ambient_state": "quiet",
        "system_health": {"status": "excellent"}
    }

    async with mqtt_client as client:
        await client.publish(
            "sentient/world/state",
            json.dumps(world_state).encode()
        )
    print("Published world state")

    print("\n⏳ Waiting up to 90 seconds for EXCITEMENT trigger activation...")
    print("   Watch logs: sudo journalctl -u sentient-proactive.service -f")


async def check_cooldowns(redis_client):
    """Check current cooldown status"""
    print("\n=== Current Cooldown Status ===")

    triggers = ["boredom", "concern", "curiosity", "care", "excitement"]

    for trigger in triggers:
        key = f"proactive:last_activation:{trigger}"
        value = await redis_client.get(key)

        if value:
            timestamp = float(value)
            elapsed = time.time() - timestamp
            print(f"{trigger.upper():12} - Last activation {int(elapsed)}s ago")
        else:
            print(f"{trigger.upper():12} - Never activated")


async def check_interaction_time(redis_client):
    """Check last interaction time"""
    print("\n=== Interaction Time ===")

    timestamp_str = await redis_client.get("interaction:last_timestamp")

    if timestamp_str:
        timestamp = float(timestamp_str)
        elapsed = time.time() - timestamp
        print(f"Last interaction: {int(elapsed)}s ago ({int(elapsed/60)} minutes)")
    else:
        print("No interaction recorded")


async def clear_all_state(redis_client):
    """Clear all proactive engine state"""
    print("\n=== Clearing All State ===")

    # Clear cooldowns
    triggers = ["boredom", "concern", "curiosity", "care", "excitement"]
    for trigger in triggers:
        key = f"proactive:last_activation:{trigger}"
        await redis_client.delete(key)
        print(f"Cleared {trigger} cooldown")

    # Clear interaction time
    await redis_client.delete("interaction:last_timestamp")
    print("Cleared interaction timestamp")

    # Clear sensor anomalies
    await redis_client.delete("sensor:anomaly_count")
    print("Cleared sensor anomaly count")

    # Clear achievements
    await redis_client.delete("system:latest_achievement")
    print("Cleared system achievement")

    print("\n✓ All state cleared")


async def subscribe_voice_output():
    """Subscribe to voice output topic to see proactive messages"""
    print("\n=== Subscribing to Voice Output ===")
    print("Listening for proactive messages on sentient/voice/tts/input...")
    print("Press Ctrl+C to stop\n")

    async with aiomqtt.Client(hostname="localhost", port=1883) as client:
        await client.subscribe("sentient/voice/tts/input")

        async for message in client.messages:
            try:
                payload = json.loads(message.payload.decode())
                if payload.get("proactive"):
                    print(f"\n{'='*60}")
                    print(f"PROACTIVE MESSAGE ({payload.get('trigger_type', 'unknown')})")
                    print(f"{'='*60}")
                    print(f"Text: {payload.get('text', '')}")
                    print(f"Priority: {payload.get('priority', 0)}")
                    print(f"Timestamp: {payload.get('timestamp', '')}")
                    print(f"{'='*60}\n")
            except Exception as e:
                print(f"Error processing message: {e}")


async def main():
    """Main test menu"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 test_proactive.py <command>")
        print("\nCommands:")
        print("  boredom      - Test BOREDOM trigger")
        print("  concern      - Test CONCERN trigger")
        print("  curiosity    - Test CURIOSITY trigger")
        print("  care         - Test CARE trigger")
        print("  excitement   - Test EXCITEMENT trigger")
        print("  cooldowns    - Check current cooldown status")
        print("  interaction  - Check last interaction time")
        print("  clear        - Clear all state")
        print("  listen       - Listen for proactive messages")
        print("  all          - Test all triggers sequentially")
        sys.exit(1)

    command = sys.argv[1].lower()

    # Connect to services
    redis_client = await setup_redis()
    mqtt_client = aiomqtt.Client(hostname="localhost", port=1883)

    try:
        if command == "boredom":
            await test_boredom_trigger(redis_client, mqtt_client)
        elif command == "concern":
            await test_concern_trigger(redis_client, mqtt_client)
        elif command == "curiosity":
            await test_curiosity_trigger(redis_client, mqtt_client)
        elif command == "care":
            await test_care_trigger(redis_client, mqtt_client)
        elif command == "excitement":
            await test_excitement_trigger(redis_client, mqtt_client)
        elif command == "cooldowns":
            await check_cooldowns(redis_client)
        elif command == "interaction":
            await check_interaction_time(redis_client)
        elif command == "clear":
            await clear_all_state(redis_client)
        elif command == "listen":
            await subscribe_voice_output()
        elif command == "all":
            print("Testing all triggers sequentially...\n")
            await test_boredom_trigger(redis_client, mqtt_client)
            await asyncio.sleep(5)
            await test_concern_trigger(redis_client, mqtt_client)
            await asyncio.sleep(5)
            await test_curiosity_trigger(redis_client, mqtt_client)
            await asyncio.sleep(5)
            await test_care_trigger(redis_client, mqtt_client)
            await asyncio.sleep(5)
            await test_excitement_trigger(redis_client, mqtt_client)
        else:
            print(f"Unknown command: {command}")
            sys.exit(1)

    finally:
        await redis_client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
