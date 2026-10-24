"""Verify voice pipeline reaches the maintained conversation MQTT handler."""
import asyncio
import importlib.util
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sentient.common import mqtt_topics


@pytest.fixture
def pipeline(monkeypatch):
    class BaseService:
        def __init__(self, name):
            self.logger = logging.getLogger(name)
            self._running = True
            self._tasks = []
            self.mqtt_publish = AsyncMock()

        def on_mqtt(self, topic):
            def decorator(callback):
                return callback
            return decorator

    substitutes = {
        "numpy": SimpleNamespace(int16=object()),
        "pyaudio": SimpleNamespace(paInt16=8, PyAudio=object, Stream=object),
        "webrtcvad": SimpleNamespace(Vad=object),
        "sentient.config": SimpleNamespace(get_config=lambda: SimpleNamespace()),
        "sentient.common.service_base": SimpleNamespace(SentientService=BaseService),
    }
    for name, module in substitutes.items():
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(__file__).resolve().parents[2] / "sentient/services/voice/pipeline.py"
    spec = importlib.util.spec_from_file_location("voice_pipeline_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.VoicePipeline()


def test_stt_routes_into_conversation_orchestrator_topic(pipeline):
    asyncio.run(pipeline._send_to_conversation("Turn the bedroom lights violet"))
    pipeline.mqtt_publish.assert_awaited()
    topic, payload = pipeline.mqtt_publish.await_args.args
    assert topic == mqtt_topics.VOICE_TRANSCRIPTION
    assert payload["text"] == "Turn the bedroom lights violet"
    assert payload["user"] == "voice_user"
    assert topic != mqtt_topics.PERSONA_CHAT_INPUT


def test_stt_output_is_forwarded_once(pipeline):
    async def scenario():
        await pipeline._handle_stt_output({
            "text": "what time is it",
            "language": "en",
            "language_probability": 0.9
        })
    asyncio.run(scenario())
    topics = [call.args[0] for call in pipeline.mqtt_publish.await_args_list]
    assert topics.count(mqtt_topics.VOICE_TRANSCRIPTION) == 1


def test_duplicate_wake_does_not_restart_active_turn(pipeline):
    pipeline._record_audio_with_vad = AsyncMock(return_value=b"wav")
    pipeline._send_to_stt = AsyncMock()

    async def scenario():
        first = asyncio.create_task(pipeline._handle_wake_detected({"confidence": 0.9}))
        await asyncio.sleep(0)
        second = asyncio.create_task(pipeline._handle_wake_detected({"confidence": 0.9}))
        await asyncio.gather(first, second)

    asyncio.run(scenario())
    assert pipeline._record_audio_with_vad.await_count == 1
    assert pipeline._send_to_stt.await_count == 1
    assert pipeline._turn_active


def test_empty_transcription_rearms(pipeline):
    pipeline._turn_active = True
    asyncio.run(pipeline._handle_stt_output({"text": ""}))
    assert not pipeline._turn_active


def test_matching_tts_completion_rearms(pipeline):
    pipeline.current_tts_id = "abc"
    pipeline._turn_active = True
    pipeline.is_cortana_speaking = True
    asyncio.run(pipeline._handle_tts_completed({"id": "abc"}))
    assert not pipeline._turn_active
    assert not pipeline.is_cortana_speaking
