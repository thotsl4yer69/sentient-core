#!/usr/bin/env python3
"""
Test script for voice-first mode components
Verifies all dependencies and MQTT connectivity
"""

import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger('VoiceTest')


def test_imports():
    """Test that all required modules can be imported"""
    logger.info("Testing imports...")

    try:
        import asyncio
        logger.info("✓ asyncio")
    except ImportError as e:
        logger.error(f"✗ asyncio: {e}")
        return False

    try:
        import pyaudio
        logger.info("✓ pyaudio")
    except ImportError as e:
        logger.error(f"✗ pyaudio: {e}")
        logger.error("  Install with: sudo apt-get install python3-pyaudio portaudio19-dev")
        return False

    try:
        import webrtcvad
        logger.info("✓ webrtcvad")
    except ImportError as e:
        logger.error(f"✗ webrtcvad: {e}")
        logger.error("  Install with: pip install webrtcvad")
        return False

    try:
        import numpy
        logger.info("✓ numpy")
    except ImportError as e:
        logger.error(f"✗ numpy: {e}")
        return False

    try:
        from aiomqtt import Client
        logger.info("✓ aiomqtt")
    except ImportError as e:
        logger.error(f"✗ aiomqtt: {e}")
        logger.error("  Install with: pip install aiomqtt")
        return False

    return True


def test_audio_devices():
    """Test audio input devices"""
    logger.info("\nTesting audio devices...")

    try:
        import pyaudio

        audio = pyaudio.PyAudio()

        # Get default input device
        try:
            default_input = audio.get_default_input_device_info()
            logger.info(f"✓ Default input device: {default_input['name']}")
            logger.info(f"  Sample rate: {int(default_input['defaultSampleRate'])}Hz")
            logger.info(f"  Input channels: {default_input['maxInputChannels']}")
        except Exception as e:
            logger.error(f"✗ No default input device: {e}")
            audio.terminate()
            return False

        # List all input devices
        logger.info("\nAvailable input devices:")
        for i in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(i)
            if info['maxInputChannels'] > 0:
                logger.info(f"  [{i}] {info['name']} ({info['maxInputChannels']} channels)")

        audio.terminate()
        return True

    except Exception as e:
        logger.error(f"✗ Audio device test failed: {e}")
        return False


def test_vad():
    """Test VAD initialization"""
    logger.info("\nTesting VAD...")

    try:
        import webrtcvad

        vad = webrtcvad.Vad(3)
        logger.info("✓ VAD initialized (aggressiveness: 3)")
        return True

    except Exception as e:
        logger.error(f"✗ VAD initialization failed: {e}")
        return False


async def test_mqtt():
    """Test MQTT connectivity"""
    logger.info("\nTesting MQTT connection...")

    try:
        from aiomqtt import Client

        try:
            client = Client(hostname="localhost", port=1883)
            await client.__aenter__()
            logger.info("✓ Connected to MQTT broker at localhost:1883")

            # Try to publish a test message
            await client.publish("sentient/test/voice", "test message")
            logger.info("✓ Published test message")

            await client.__aexit__(None, None, None)
            logger.info("✓ Disconnected cleanly")
            return True

        except Exception as e:
            logger.error(f"✗ MQTT connection failed: {e}")
            logger.error("  Make sure mosquitto is running: sudo systemctl status mosquitto")
            return False

    except ImportError as e:
        logger.error(f"✗ Cannot import MQTT client: {e}")
        return False


def main():
    """Run all tests"""
    logger.info("=" * 60)
    logger.info("Voice-First Mode Component Test")
    logger.info("=" * 60)

    results = []

    # Test imports
    results.append(("Imports", test_imports()))

    # Test audio
    results.append(("Audio Devices", test_audio_devices()))

    # Test VAD
    results.append(("VAD", test_vad()))

    # Test MQTT
    import asyncio
    results.append(("MQTT", asyncio.run(test_mqtt())))

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("Test Summary")
    logger.info("=" * 60)

    all_passed = True
    for name, passed in results:
        status = "✓ PASSED" if passed else "✗ FAILED"
        logger.info(f"{name:20s} {status}")
        if not passed:
            all_passed = False

    logger.info("=" * 60)

    if all_passed:
        logger.info("\n✓ All tests passed! Voice-first mode is ready to use.")
        return 0
    else:
        logger.error("\n✗ Some tests failed. Please fix the issues above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
