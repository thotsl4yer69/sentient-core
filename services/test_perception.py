#!/usr/bin/env python3
"""
Test script for perception layer service

Tests:
1. MQTT connectivity
2. Message subscription and handling
3. World state generation
4. Audio monitoring
5. Time awareness
6. Threat detection
"""

import asyncio
import json
import time
from datetime import datetime

import aiomqtt


async def test_mqtt_connection(broker: str = "localhost", port: int = 1883):
    """Test MQTT broker connection"""
    print("Testing MQTT connection...")
    try:
        async with aiomqtt.Client(hostname=broker, port=port) as client:
            print(f"✅ Connected to MQTT broker at {broker}:{port}")
            return True
    except Exception as e:
        print(f"❌ Failed to connect to MQTT broker: {e}")
        return False


async def test_world_state_subscription(broker: str = "localhost", port: int = 1883, timeout: int = 15):
    """Test world state subscription and reception"""
    print(f"\nTesting world state subscription (waiting {timeout}s for message)...")

    try:
        async with aiomqtt.Client(hostname=broker, port=port) as client:
            await client.subscribe("sentient/world/state")
            print("✅ Subscribed to sentient/world/state")

            # Wait for message with timeout
            start_time = time.time()
            async for message in client.messages:
                if time.time() - start_time > timeout:
                    print(f"⚠️ No message received within {timeout}s")
                    return False

                try:
                    payload = json.loads(message.payload.decode())
                    print("\n✅ Received world state message:")
                    print(json.dumps(payload, indent=2))

                    # Validate structure
                    required_fields = [
                        "timestamp",
                        "jack_present",
                        "jack_location",
                        "threat_level",
                        "active_threats",
                        "ambient_state",
                        "time_context",
                        "last_interaction_seconds",
                        "system_health"
                    ]

                    missing_fields = [f for f in required_fields if f not in payload]
                    if missing_fields:
                        print(f"❌ Missing required fields: {missing_fields}")
                        return False

                    print("✅ All required fields present")

                    # Validate data types
                    assert isinstance(payload["jack_present"], bool)
                    assert isinstance(payload["threat_level"], int)
                    assert 0 <= payload["threat_level"] <= 10
                    assert isinstance(payload["active_threats"], list)
                    assert payload["ambient_state"] in ["quiet", "active", "noisy"]
                    assert payload["time_context"] in ["morning", "afternoon", "evening", "night"]
                    assert isinstance(payload["system_health"], dict)

                    print("✅ Data types valid")
                    return True

                except json.JSONDecodeError as e:
                    print(f"❌ Failed to decode JSON: {e}")
                    return False
                except AssertionError as e:
                    print(f"❌ Data validation failed: {e}")
                    return False

    except Exception as e:
        print(f"❌ Error in world state subscription: {e}")
        return False


async def send_test_vision_detection(broker: str = "localhost", port: int = 1883):
    """Send test vision detection message"""
    print("\nSending test vision detection...")

    try:
        async with aiomqtt.Client(hostname=broker, port=port) as client:
            test_payload = {
                "timestamp": datetime.now().isoformat(),
                "classes": ["person", "jack"],
                "confidence": 0.95,
                "location": "front_door",
                "person_id": "jack",
                "bounding_box": {
                    "x": 100,
                    "y": 100,
                    "width": 200,
                    "height": 400
                }
            }

            await client.publish(
                "sentient/sensor/vision/camera_front/detection",
                payload=json.dumps(test_payload),
                qos=1
            )

            print("✅ Test vision detection sent")
            print(json.dumps(test_payload, indent=2))
            return True

    except Exception as e:
        print(f"❌ Failed to send test vision detection: {e}")
        return False


async def send_test_rf_detection(broker: str = "localhost", port: int = 1883):
    """Send test RF detection message"""
    print("\nSending test RF detection...")

    try:
        async with aiomqtt.Client(hostname=broker, port=port) as client:
            test_payload = {
                "timestamp": datetime.now().isoformat(),
                "known_device": True,
                "owner": "jack",
                "mac_address": "AA:BB:CC:DD:EE:FF",
                "rssi": -45,
                "location": "living_room",
                "jamming_detected": False
            }

            await client.publish(
                "sentient/sensor/rf/detection",
                payload=json.dumps(test_payload),
                qos=1
            )

            print("✅ Test RF detection sent")
            print(json.dumps(test_payload, indent=2))
            return True

    except Exception as e:
        print(f"❌ Failed to send test RF detection: {e}")
        return False


async def send_test_system_status(broker: str = "localhost", port: int = 1883):
    """Send test system status message"""
    print("\nSending test system status...")

    try:
        async with aiomqtt.Client(hostname=broker, port=port) as client:
            test_payload = {
                "timestamp": datetime.now().isoformat(),
                "node_id": "orin-avatar",
                "online": True,
                "cpu_percent": 45.2,
                "memory_percent": 62.8,
                "temperature": 58.5,
                "uptime": 86400
            }

            await client.publish(
                "sentient/system/status",
                payload=json.dumps(test_payload),
                qos=1
            )

            print("✅ Test system status sent")
            print(json.dumps(test_payload, indent=2))
            return True

    except Exception as e:
        print(f"❌ Failed to send test system status: {e}")
        return False


async def test_threat_detection(broker: str = "localhost", port: int = 1883):
    """Test threat detection by sending suspicious detection"""
    print("\nTesting threat detection...")

    try:
        async with aiomqtt.Client(hostname=broker, port=port) as client:
            # Send unknown person detection
            threat_payload = {
                "timestamp": datetime.now().isoformat(),
                "classes": ["unknown_person"],
                "confidence": 0.92,
                "location": "backyard"
            }

            await client.publish(
                "sentient/sensor/vision/camera_back/detection",
                payload=json.dumps(threat_payload),
                qos=1
            )

            print("✅ Threat detection test message sent")
            print(json.dumps(threat_payload, indent=2))

            # Wait for world state update
            print("\nWaiting for world state update with threat...")
            await asyncio.sleep(6)

            # Subscribe and check
            await client.subscribe("sentient/world/state")
            async for message in client.messages:
                payload = json.loads(message.payload.decode())

                print("\nWorld state after threat:")
                print(f"  Threat level: {payload['threat_level']}")
                print(f"  Active threats: {len(payload['active_threats'])}")

                if payload['threat_level'] > 0:
                    print("✅ Threat detection working")
                    return True
                else:
                    print("⚠️ No threat detected in world state")
                    return False

    except Exception as e:
        print(f"❌ Threat detection test failed: {e}")
        return False


async def run_all_tests():
    """Run all perception layer tests"""
    print("=" * 60)
    print("SENTIENT CORE PERCEPTION LAYER TEST SUITE")
    print("=" * 60)

    results = {}

    # Test 1: MQTT Connection
    results["mqtt_connection"] = await test_mqtt_connection()

    if not results["mqtt_connection"]:
        print("\n❌ MQTT connection failed. Cannot proceed with other tests.")
        print("Make sure Mosquitto broker is running: sudo systemctl start mosquitto")
        return

    # Test 2: Send test messages
    results["vision_message"] = await send_test_vision_detection()
    await asyncio.sleep(1)

    results["rf_message"] = await send_test_rf_detection()
    await asyncio.sleep(1)

    results["system_message"] = await send_test_system_status()
    await asyncio.sleep(1)

    # Test 3: World state subscription
    results["world_state"] = await test_world_state_subscription()

    # Test 4: Threat detection
    results["threat_detection"] = await test_threat_detection()

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed!")
    else:
        print(f"\n⚠️ {total - passed} test(s) failed")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
