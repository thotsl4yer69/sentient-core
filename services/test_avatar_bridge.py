#!/usr/bin/env python3
"""
Avatar Bridge Test Script
Demonstrates avatar bridge functionality with simulated cognitive events.
"""

import asyncio
import json
import sys
from datetime import datetime

try:
    from aiomqtt import Client as MQTTClient
except ImportError:
    print("ERROR: aiomqtt is required. Install with: pip install aiomqtt")
    sys.exit(1)

MQTT_BROKER = "localhost"
MQTT_PORT = 1883

# Test scenarios
SCENARIOS = [
    {
        "name": "Wake Word Detection",
        "events": [
            {
                "topic": "sentient/wake/detected",
                "payload": {
                    "timestamp": datetime.now().timestamp(),
                    "confidence": 0.95
                },
                "description": "Wake word detected - should snap to alert"
            }
        ]
    },
    {
        "name": "Happy Conversation",
        "events": [
            {
                "topic": "sentient/emotion/state",
                "payload": {
                    "emotion": "happy",
                    "intensity": 0.8,
                    "timestamp": datetime.now().timestamp()
                },
                "description": "Setting happy emotion"
            },
            {
                "topic": "sentient/conversation/response",
                "payload": {
                    "message": "Hello Jack! How can I help you today?",
                    "timestamp": datetime.now().timestamp()
                },
                "description": "Cortana speaking - should activate lip sync"
            },
            {
                "topic": "sentient/tts/synthesize",
                "payload": {
                    "event": "start",
                    "timestamp": datetime.now().timestamp()
                },
                "description": "TTS started"
            }
        ]
    },
    {
        "name": "Thoughtful Contemplation",
        "events": [
            {
                "topic": "sentient/conversation/thinking",
                "payload": {
                    "is_thinking": True,
                    "topic": "analyzing user request",
                    "stage": "reasoning",
                    "confidence": 0.7,
                    "timestamp": datetime.now().timestamp()
                },
                "description": "Entering thinking state"
            },
            {
                "topic": "sentient/emotion/state",
                "payload": {
                    "emotion": "thoughtful",
                    "intensity": 0.6,
                    "timestamp": datetime.now().timestamp()
                },
                "description": "Thoughtful emotion during contemplation"
            }
        ]
    },
    {
        "name": "Concerned State",
        "events": [
            {
                "topic": "sentient/emotion/state",
                "payload": {
                    "emotion": "concerned",
                    "intensity": 0.7,
                    "timestamp": datetime.now().timestamp()
                },
                "description": "Concerned about potential issue"
            }
        ]
    },
    {
        "name": "Return to Neutral",
        "events": [
            {
                "topic": "sentient/emotion/state",
                "payload": {
                    "emotion": "neutral",
                    "intensity": 0.5,
                    "timestamp": datetime.now().timestamp()
                },
                "description": "Returning to neutral/idle state"
            },
            {
                "topic": "sentient/conversation/thinking",
                "payload": {
                    "is_thinking": False,
                    "timestamp": datetime.now().timestamp()
                },
                "description": "Stopped thinking"
            },
            {
                "topic": "sentient/tts/synthesize",
                "payload": {
                    "event": "end",
                    "timestamp": datetime.now().timestamp()
                },
                "description": "TTS ended - stop speaking"
            }
        ]
    }
]


async def run_test_scenario(client: MQTTClient, scenario: dict):
    """Run a single test scenario"""
    print(f"\n{'='*60}")
    print(f"Scenario: {scenario['name']}")
    print('='*60)

    for event in scenario['events']:
        print(f"\n→ {event['description']}")
        print(f"  Topic: {event['topic']}")
        print(f"  Payload: {json.dumps(event['payload'], indent=4)}")

        # Publish event
        await client.publish(
            event['topic'],
            json.dumps(event['payload'])
        )

        # Wait for avatar to process
        await asyncio.sleep(1.0)

    # Wait between scenarios
    print(f"\nWaiting 3 seconds before next scenario...")
    await asyncio.sleep(3.0)


async def monitor_avatar_output(client: MQTTClient):
    """Monitor avatar output topics"""
    print("\n" + "="*60)
    print("Monitoring Avatar Output Topics")
    print("="*60 + "\n")

    avatar_topics = [
        "sentient/persona/emotion",
        "sentient/persona/speaking",
        "sentient/persona/attention",
        "sentient/persona/idle"
    ]

    try:
        async with client.messages() as messages:
            for topic in avatar_topics:
                await client.subscribe(topic)
                print(f"Subscribed to: {topic}")

            print("\nListening for avatar updates (Ctrl+C to stop)...\n")

            async for message in messages:
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                topic = message.topic.value
                payload = message.payload.decode()

                try:
                    data = json.loads(payload)
                    formatted = json.dumps(data, indent=2)
                    print(f"[{timestamp}] {topic}")
                    print(f"{formatted}\n")
                except json.JSONDecodeError:
                    print(f"[{timestamp}] {topic}: {payload}\n")

    except asyncio.CancelledError:
        print("\nMonitoring stopped")


async def run_interactive_test():
    """Run interactive test with user control"""
    print("""
╔════════════════════════════════════════════════════════════════╗
║           Avatar Bridge Interactive Test Suite                ║
╚════════════════════════════════════════════════════════════════╝

This test will:
1. Connect to MQTT broker
2. Monitor avatar output topics
3. Send test events to trigger avatar behaviors
4. Display avatar responses in real-time

Make sure avatar bridge service is running:
  sudo systemctl status sentient-avatar-bridge
""")

    input("Press Enter to start test...")

    async with MQTTClient(MQTT_BROKER, MQTT_PORT) as client:
        print(f"\nConnected to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}")

        # Start monitoring task
        monitor_task = asyncio.create_task(monitor_avatar_output(client))

        # Wait a moment for subscription
        await asyncio.sleep(1.0)

        # Run test scenarios
        try:
            for scenario in SCENARIOS:
                async with MQTTClient(MQTT_BROKER, MQTT_PORT) as test_client:
                    await run_test_scenario(test_client, scenario)

            print("\n" + "="*60)
            print("All test scenarios completed!")
            print("="*60)
            print("\nContinuing to monitor avatar output...")
            print("Press Ctrl+C to exit\n")

            # Keep monitoring
            await monitor_task

        except KeyboardInterrupt:
            print("\n\nTest interrupted by user")
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass


async def main():
    """Main entry point"""
    try:
        await run_interactive_test()
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTest terminated by user")
        sys.exit(0)
