"""Drive two full wake->STT->chat->Piper->idle turns over a fake MQTT bus.

Keeps the real production handlers, turn IDs, worker orchestration and MQTT
payload serialization; only microphone/model/player/LLM boundaries are mocked.
"""
import asyncio
import importlib.util
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sentient.common import mqtt_topics


ROOT = Path(__file__).resolve().parents[2]


def load_source(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MQTTBus:
    def __init__(self):
        self.handlers = {}
        self.events = []

    def register(self, service):
        for topic, handler in service.handlers.items():
            self.handlers.setdefault(topic, []).append(handler)
        service.mqtt_publish = self.publish

    async def publish(self, topic, data):
        self.events.append((topic, data))
        raw = json.dumps(data).encode("utf-8")
        for handler in self.handlers.get(topic, ()):
            await handler(topic, raw)


def test_two_full_spoken_turns_rearm_and_preserve_ids(monkeypatch):
    class Base:
        def __init__(self, name):
            self.logger = logging.getLogger(name)
            self._running = True
            self.handlers = {}
            self._tasks = []

        def on_mqtt(self, topic):
            def decorate(fn):
                self.handlers[topic] = fn
                return fn
            return decorate

    replacements = {
        "pyaudio": SimpleNamespace(paInt16=8, PyAudio=object, Stream=object),
        "webrtcvad": SimpleNamespace(Vad=object),
        "sentient.config": SimpleNamespace(get_config=lambda: SimpleNamespace()),
        "sentient.common.service_base": SimpleNamespace(SentientService=Base),
    }
    for name, value in replacements.items():
        monkeypatch.setitem(sys.modules, name, value)
    pipeline_mod = load_source("end_to_end_pipeline",
                               "sentient/services/voice/pipeline.py")
    stt_mod = load_source("end_to_end_stt",
                          "sentient/services/voice/stt_worker.py")
    tts_mod = load_source("end_to_end_tts",
                          "sentient/services/voice/tts_worker.py")

    bus = MQTTBus()
    pipeline = pipeline_mod.VoicePipeline()
    stt = stt_mod.WhisperSTTWorker()
    tts = tts_mod.PiperTTSWorker()

    stt_mod.decode_voice_wav = lambda encoded: [0.1, 0.2, 0.3]
    stt._transcribe = lambda samples: ("What time is it", 1.0)
    pipeline._record_audio_with_vad = AsyncMock(return_value=b"mock-wav")

    async def fake_audio(cmd, stdin=None):
        return None

    tts._run_speech_process = fake_audio
    bus.register(pipeline)
    bus.register(stt)
    bus.register(tts)

    async def conversation(topic, raw):
        request = json.loads(raw)
        turn_id = request.get("turn_id")
        await bus.publish(mqtt_topics.CHAT_OUTPUT, {
            "text": "It is 1 PM.", "user": "voice_user", "turn_id": turn_id,
        })
        await bus.publish(mqtt_topics.TTS_SPEAK, {
            "text": "It is 1 PM.", "turn_id": turn_id,
        })

    bus.handlers[mqtt_topics.VOICE_TRANSCRIPTION] = [conversation]

    async def scenario():
        for _ in range(2):
            await bus.publish(mqtt_topics.WAKE_WORD_DETECTED, {"confidence": 0.91})
            assert not pipeline._turn_active
            assert pipeline._turn_id is None
        wake_turns = [payload["turn_id"] for topic, payload in bus.events
                      if topic == mqtt_topics.STT_AUDIO_INPUT]
        assert len(wake_turns) == len(set(wake_turns)) == 2
        outputs = [(topic, payload) for topic, payload in bus.events
                   if topic == mqtt_topics.TTS_COMPLETED]
        assert len(outputs) == 2
        assert [payload["turn_id"] for _, payload in outputs] == wake_turns
        assert all(payload["status"] == "completed" for _, payload in outputs)
        count = len([1 for topic, _ in bus.events if topic == mqtt_topics.VOICE_TRANSCRIPTION])
        assert count == 2

    asyncio.run(scenario())
