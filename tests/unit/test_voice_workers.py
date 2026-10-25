"""Isolated STT/Piper adapters: no microphones, models or speakers required."""
import asyncio
import base64
import io
import json
import struct
import wave

import pytest
from sentient.common import mqtt_topics
from sentient.services.voice.stt_worker import WhisperSTTWorker, decode_voice_wav
from sentient.services.voice.tts_worker import PiperTTSWorker


def run(coro):
    return asyncio.run(coro)


def wav_audio(frames=1600):
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(struct.pack("<" + "h" * frames, *([8000] * frames)))
    return base64.b64encode(out.getvalue()).decode("ascii")


def test_wave_decoder_preserves_duration_and_pcm_scale():
    samples = decode_voice_wav(wav_audio())
    assert len(samples) == 1600
    assert 0.24 < float(samples[0]) < 0.25


@pytest.mark.parametrize("blob", ["not valid !", base64.b64encode(b"bad wav").decode()])
def test_wave_decoder_rejects_invalid_frames(blob):
    with pytest.raises(ValueError):
        decode_voice_wav(blob)


def test_whisper_worker_carries_turn_id_and_no_false_success():
    worker = WhisperSTTWorker()
    published = []

    async def publish(topic, payload):
        published.append((topic, payload))

    def transcribe(samples):
        assert len(samples) == 1600
        return "set the lights purple", 0.98

    worker.mqtt_publish = publish
    worker._transcribe = transcribe
    run(worker.handle_audio({"turn_id": "w-1", "audio": {"format": "wav", "data": wav_audio()}}))
    assert len(published) == 1
    assert published[0][0] == mqtt_topics.STT_OUTPUT
    assert published[0][1]["turn_id"] == "w-1"
    assert published[0][1]["text"] == "set the lights purple"
    assert published[0][1]["duration_ms"] >= 0


def test_whisper_worker_reports_bad_input_as_empty_transcript():
    worker = WhisperSTTWorker()
    published = []

    async def publish(topic, payload):
        published.append(payload)

    worker.mqtt_publish = publish
    run(worker.handle_audio({"turn_id": "bad", "audio": {"format": "wav", "data": "!"}}))
    assert published[0]["text"] == ""
    assert published[0]["turn_id"] == "bad"
    assert published[0].get("error")


def test_piper_worker_emits_matching_start_and_completion_without_playback_hardware():
    worker = PiperTTSWorker()
    worker.voice_model = "/does/not/require/real/model/on_mock"
    worker.piper_bin = "mock-piper"
    worker.player_bin = "mock-aplay"
    published = []
    commands = []

    async def publish(topic, payload):
        published.append((topic, payload))

    async def fake_run(cmd, stdin=None):
        commands.append((cmd, stdin))

    worker.mqtt_publish = publish
    worker._run_speech_process = fake_run
    run(worker.speak({"text": "Hello", "turn_id": "t-4"}))
    assert [topic for topic, _ in published] == [
        mqtt_topics.TTS_STARTED, mqtt_topics.TTS_COMPLETED
    ]
    assert published[0][1]["id"] == published[1][1]["id"]
    assert published[0][1]["turn_id"] == published[1][1]["turn_id"] == "t-4"
    assert published[1][1]["status"] == "completed"
    assert len(commands) == 2
    assert commands[0][1] == b"Hello"


def test_piper_worker_announces_error_completion_even_if_synthesis_fails():
    worker = PiperTTSWorker()
    worker.voice_model = "/mock/voice.onnx"
    reported = []

    async def publish(topic, payload):
        reported.append((topic, payload))

    async def fail(cmd, stdin=None):
        raise OSError("mock Piper unavailable")

    worker.mqtt_publish = publish
    worker._run_speech_process = fail
    run(worker.speak({"text": "Hi", "turn_id": "fail-1"}))
    assert reported[-1][0] == mqtt_topics.TTS_COMPLETED
    assert reported[-1][1]["status"] == "error"
    assert reported[-1][1]["turn_id"] == "fail-1"


def test_worker_fails_closed_without_local_stt_model():
    worker = WhisperSTTWorker()
    worker.model_path = "/not/a/local/model"
    with pytest.raises(RuntimeError):
        run(worker.setup())


def test_worker_fails_closed_without_voice_model():
    worker = PiperTTSWorker()
    worker.voice_model = "/not/a/voice/model.onnx"
    with pytest.raises(RuntimeError):
        run(worker.setup())
