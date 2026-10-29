"""Avatar must follow actual Piper playback, not LLM text or stale events."""
import asyncio
import json
from unittest.mock import AsyncMock

from sentient.services.avatar.bridge import AvatarBridgeService


def test_avatar_starts_and_stops_with_real_tts_events():
    async def scenario():
        bridge = AvatarBridgeService()
        bridge._publish_and_broadcast = AsyncMock()
        await bridge._handle_conversation_message(
            json.dumps({"state": "responding"}).encode()
        )
        assert not bridge.state.speaking

        await bridge._handle_tts_started(
            json.dumps({"id": "tts-1", "turn_id": "turn-1", "duration": 1.0}).encode()
        )
        assert bridge.state.speaking
        assert bridge._tts_id == "tts-1"

        # Late completion of an unrelated request must not close this animation.
        await bridge._handle_tts_completed(
            json.dumps({"id": "tts-old", "turn_id": "turn-old"}).encode()
        )
        assert bridge.state.speaking

        await bridge._handle_tts_completed(
            json.dumps({"id": "tts-1", "turn_id": "turn-1"}).encode()
        )
        assert not bridge.state.speaking
        assert bridge._tts_id is None

        if bridge._speaking_timeout:
            bridge._speaking_timeout.cancel()

    asyncio.run(scenario())


def test_avatar_ignores_stale_completions_from_previous_turn():
    async def scenario():
        bridge = AvatarBridgeService()
        bridge._publish_and_broadcast = AsyncMock()
        await bridge._handle_tts_started(
            json.dumps({"id": "audio-A", "turn_id": "turn-A"}).encode()
        )
        await bridge._handle_tts_started(
            json.dumps({"id": "audio-B", "turn_id": "turn-B"}).encode()
        )
        await bridge._handle_tts_completed(
            json.dumps({"id": "audio-A", "turn_id": "turn-A"}).encode()
        )
        assert bridge.state.speaking
        assert bridge._tts_id == "audio-B"
        await bridge._handle_tts_completed(
            json.dumps({"id": "audio-B", "turn_id": "turn-B"}).encode()
        )
        assert not bridge.state.speaking
    asyncio.run(scenario())
