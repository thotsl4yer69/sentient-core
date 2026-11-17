"""Start a real mosquitto and collect messages with aiomqtt."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import tempfile
import time
import uuid
from typing import Callable

from aiomqtt import Client


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Mosquitto:
    """Anonymous broker on 127.0.0.1, gone when the context exits."""

    def __init__(self):
        self.port = 0
        self._proc: subprocess.Popen | None = None
        self._conf = ""

    async def __aenter__(self) -> "Mosquitto":
        self.port = _free_port()
        handle = tempfile.NamedTemporaryFile("w", delete=False, suffix=".conf")
        handle.write(
            f"listener {self.port} 127.0.0.1\n"
            "allow_anonymous true\n"
            "persistence false\n"
        )
        handle.close()
        self._conf = handle.name
        self._proc = subprocess.Popen(
            ["mosquitto", "-c", self._conf],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    err = self._proc.stderr.read().decode() if self._proc.stderr else ""
                    raise RuntimeError(f"mosquitto exited: {err}")
                try:
                    with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                        return self
                except OSError:
                    await asyncio.sleep(0.05)
            raise RuntimeError("mosquitto did not accept connections")
        except Exception:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, exc_type, exc, tb) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
            if proc.stderr is not None:
                proc.stderr.close()
        if self._conf and os.path.exists(self._conf):
            os.unlink(self._conf)
            self._conf = ""


class Collector:
    """Subscribe before the code under test publishes."""

    def __init__(self, port: int, topics: list[tuple[str, int]]):
        self.port = port
        self.topics = topics
        self.messages: list[tuple[str, dict]] = []
        self._cm: Client | None = None
        self._client: Client | None = None
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> "Collector":
        self._cm = Client(
            hostname="127.0.0.1",
            port=self.port,
            identifier=f"collector-{uuid.uuid4().hex[:8]}",
        )
        self._client = await self._cm.__aenter__()
        for topic, qos in self.topics:
            await self._client.subscribe(topic, qos=qos)
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._cm is not None:
            await self._cm.__aexit__(exc_type, exc, tb)

    async def _run(self) -> None:
        assert self._client is not None
        async for message in self._client.messages:
            try:
                payload = json.loads(bytes(message.payload).decode())
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {"_raw": bytes(message.payload).decode(errors="replace")}
            if isinstance(payload, dict):
                self.messages.append((str(message.topic), payload))

    async def publish(self, topic: str, payload: dict, qos: int = 1) -> None:
        assert self._client is not None
        await self._client.publish(topic, json.dumps(payload).encode(), qos=qos)

    async def wait_until(self, predicate: Callable[[list[tuple[str, dict]]], bool], timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(self.messages):
                return
            await asyncio.sleep(0.02)
        raise AssertionError(f"timed out waiting for MQTT messages; saw {self.messages!r}")
