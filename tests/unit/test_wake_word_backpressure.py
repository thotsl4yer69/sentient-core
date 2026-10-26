"""Exercise real wake-service control flow without a mic, model, or MQTT broker."""

import asyncio
import importlib.util
import logging
from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def service(monkeypatch):
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

    # Only hardware/framework boundaries are substituted. Queue, threads,
    # asyncio, capture loop, detection loop and cooldown are production code.
    substitutes = {
        "numpy": SimpleNamespace(frombuffer=lambda data, dtype: data, int16=object()),
        "pyaudio": SimpleNamespace(paInt16=8, PyAudio=object, Stream=object),
        "openwakeword.model": SimpleNamespace(Model=object),
        "sentient.config": SimpleNamespace(get_config=lambda: SimpleNamespace(
            wake_word=SimpleNamespace(sensitivity=0.5, model="test")
        )),
        "sentient.common.service_base": SimpleNamespace(SentientService=BaseService),
    }
    for name, module in substitutes.items():
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(__file__).resolve().parents[2] / "sentient/services/voice/wake_word.py"
    spec = importlib.util.spec_from_file_location("wake_service_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    instance = module.WakeWordService()
    return module, instance


def capture(instance, frames):
    frames = iter(frames)

    def read(*args, **kwargs):
        try:
            return next(frames)
        except StopIteration:
            instance._running = False
            raise OSError("test capture ended")

    instance.stream = SimpleNamespace(read=read)
    instance._running = True
    worker = threading.Thread(target=instance._audio_capture_thread, daemon=True)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive(), "capture blocked behind a full queue"


def drain(instance):
    result = []
    while True:
        try:
            result.append(instance.audio_queue.get_nowait())
        except queue.Empty:
            return result


def test_stalled_consumer_keeps_only_latest_audio(service, monkeypatch):
    module, instance = service
    scheduled_puts = 0

    def record_pending_put(coroutine, loop):
        nonlocal scheduled_puts
        scheduled_puts += 1
        coroutine.close()  # Model a stalled loop without leaking test coroutines.

    # The old setup stored a running event loop before starting capture.
    # Supply its handle so the regression exercises that original path too.
    instance._loop = object()
    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", record_pending_put)
    capture(instance, range(20_000))
    assert scheduled_puts == 0, "capture queued pending work on a stalled event loop"
    assert instance.audio_queue.qsize() == 5
    assert drain(instance) == list(range(19_995, 20_000))


def test_fifty_capture_recovery_cycles_do_not_replay_old_audio(service):
    _, instance = service
    for turn in range(50):
        start = turn * 100
        capture(instance, range(start, start + 100))
        assert drain(instance) == list(range(start + 95, start + 100))


def test_uncongested_audio_order_is_preserved(service):
    _, instance = service
    capture(instance, [b"one", b"two", b"three"])
    assert drain(instance) == [b"one", b"two", b"three"]


def test_read_failure_backs_off_instead_of_spinning(service, monkeypatch):
    module, instance = service
    sleeps = []

    def broken_read(*args, **kwargs):
        raise OSError("microphone disconnected")

    def backoff(seconds):
        sleeps.append(seconds)
        instance._running = False

    instance.stream = SimpleNamespace(read=broken_read)
    monkeypatch.setattr(module.time, "sleep", backoff)
    instance._audio_capture_thread()
    assert sleeps == [0.1]
    assert instance.audio_queue.empty()


def test_cooldown_survives_wall_clock_adjustments(service, monkeypatch):
    module, instance = service
    ticks = iter([10.0, 11.0, 12.1])
    wall_times = iter([1000.0, 1.0])
    monkeypatch.setattr(module, "time", SimpleNamespace(
        monotonic=lambda: next(ticks), time=lambda: next(wall_times)
    ))

    async def scenario():
        for _ in range(3):
            await instance._publish_detection(0.9)

    asyncio.run(scenario())
    calls = instance.mqtt_publish.await_args_list
    assert len(calls) == 2
    assert [call.args[1]["timestamp"] for call in calls] == [1000.0, 1.0]


def test_detection_consumes_thread_queue_and_publishes(service):
    _, instance = service
    seen = []
    instance.audio_queue.put_nowait(b"audio")

    def predict(frame):
        seen.append(frame)
        instance._running = False
        return {"test": 0.9}

    instance.oww_model = SimpleNamespace(predict=predict)
    instance.active_model_key = "test"
    asyncio.run(instance._detection_loop())
    assert seen == [b"audio"]
    instance.mqtt_publish.assert_awaited_once()


def test_empty_queue_allows_other_tasks_and_cancellation(service):
    _, instance = service

    async def scenario():
        task = asyncio.create_task(instance._detection_loop())
        await asyncio.sleep(0.01)
        assert not task.done()
        instance._running = False
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

def test_configured_model_only_accepts_exact_matching_prediction(service):
    module, instance = service
    triggered = []
    instance.oww_model = SimpleNamespace(
        predict=lambda frame: {"other_model": 0.99, "hey_jarvis_v0.1": 0.85}
    )
    instance.active_model_key = "hey_jarvis_v0.1"

    async def publish(confidence):
        triggered.append(confidence)
        instance._running = False

    instance._publish_detection = publish
    instance.audio_queue.put_nowait(b"audio")
    asyncio.run(instance._detection_loop())
    assert triggered == [0.85]


def test_model_initialization_rejects_nonexistent_phrase(service, monkeypatch):
    module, instance = service
    instance.wake_model_name = "not_a_trained_model"
    monkeypatch.setattr(module, "Model", lambda **kw: SimpleNamespace(
        models={"hey_jarvis_v0.1": object()}
    ))
    assert not instance._initialize_model()
    assert instance.active_model_key is None


def test_model_initialization_selects_hey_jarvis(service, monkeypatch):
    module, instance = service
    instance.wake_model_name = "hey_jarvis"
    monkeypatch.setattr(module, "Model", lambda **kw: SimpleNamespace(
        models={"hey_jarvis_v0.1": object(), "alexa": object()}
    ))
    assert instance._initialize_model()
    assert instance.active_model_key == "hey_jarvis_v0.1"


def test_speech_gate_blocks_then_rearms(service, monkeypatch):
    module, instance = service
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    instance._speech_active = True
    instance._gate_updated = clock[0]
    assert instance._should_suspend()
    instance._speech_active = False
    instance._suspended_until = clock[0] + 0.3
    assert instance._should_suspend()
    clock[0] += 0.31
    assert not instance._should_suspend()


def test_airi_candidate_falls_back_to_existing_jarvis_model(service, monkeypatch):
    module, instance = service
    instance.wake_model_name = "/opt/sentient-core/models/wake/hey_airi.onnx"
    instance.requested_phrase = "Hey AIRI"
    instance.fallback_model_name = "hey_jarvis"
    instance.fallback_phrase = "Hey Jarvis"
    monkeypatch.setattr(module, "Model", lambda **kw: SimpleNamespace(
        models={"hey_jarvis_v0.1": object()}
    ))
    assert instance._initialize_model()
    assert instance.active_model_key == "hey_jarvis_v0.1"
    assert instance.active_phrase == "Hey Jarvis"


def test_airi_candidate_switches_only_when_real_trained_classifier_exists(service, monkeypatch, tmp_path):
    module, instance = service
    model_file = tmp_path / "hey_airi.onnx"
    model_file.write_bytes(b"dummy model validated by mocked loader")
    instance.wake_model_name = str(model_file)
    instance.requested_phrase = "Hey AIRI"
    instance.fallback_model_name = "hey_jarvis"
    instance.fallback_phrase = "Hey Jarvis"
    loaded = []
    def fake_loader(**kwargs):
        loaded.append(kwargs)
        return SimpleNamespace(models={"hey_airi": object()})
    monkeypatch.setattr(module, "Model", fake_loader)
    assert instance._initialize_model()
    assert instance.active_phrase == "Hey AIRI"
    assert instance.active_model_key == "hey_airi"
    assert len(loaded) == 1
    assert loaded[0]["wakeword_models"] == [str(model_file)]


def test_missing_both_models_fails_closed(service, monkeypatch):
    module, instance = service
    instance.wake_model_name = "not_installed"
    instance.fallback_model_name = "not_installed_either"
    monkeypatch.setattr(module, "Model", lambda **kwargs: SimpleNamespace(models={}))
    assert not instance._initialize_model()
    assert instance.active_phrase is None
    assert instance.active_model_key is None
