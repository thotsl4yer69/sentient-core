"""Regression: slow voice/LLM work must not starve MQTT events."""
import asyncio
from types import SimpleNamespace

from sentient.common import service_base


def test_mqtt_reader_dispatches_two_slow_handlers_concurrently(monkeypatch):
    finished = []
    started = []
    async def scenario():
        both_started = asyncio.Event()
        release = asyncio.Event()

        service = service_base.SentientService("mqtt_contract_test")
        service._running = True
        service._max_inflight = 2

        @service.on_mqtt("sentient/test/input")
        async def on_test(topic, payload):
            started.append(payload)
            if len(started) == 2:
                both_started.set()
            await release.wait()
            finished.append(payload)

        class Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def subscribe(self, topic):
                return None

            @property
            def messages(self):
                return self.events()

            async def events(self):
                for value in (b"one", b"two"):
                    yield SimpleNamespace(topic="sentient/test/input", payload=value)
                await asyncio.wait_for(both_started.wait(), timeout=1.0)
                release.set()
                await asyncio.sleep(0.01)
                service._running = False

        monkeypatch.setattr(service_base, "MQTTClient", Client)
        await asyncio.wait_for(service._mqtt_loop(), timeout=2.0)
        assert started == [b"one", b"two"]
        assert finished == [b"one", b"two"]
        assert not service._inflight

    asyncio.run(scenario())
