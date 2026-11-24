"""Fixtures for sc-core tests.

* ``encoder``: the real MiniLM ONNX encoder (skipped if the model files are absent;
  fetch with ``python -m sc.embed --fetch``).
* ``fake_llm``: a scripted OpenAI-compatible SSE server on loopback (test-only),
  for deterministic deadline, cancel and pre-emption tests. It records whether
  the client disconnected mid-stream.
* ``real_llm_url``: a real llama.cpp ``llama-server``. Set ``SC_TEST_LLM_URL`` to
  use a running one, or ``SC_TEST_LLAMA_SERVER`` + ``SC_TEST_GGUF`` to start one.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import web

from sc.auth import parse_tokens


@pytest.fixture(scope="session")
def encoder():
    from sc.embed import MiniLMEncoder

    try:
        return MiniLMEncoder()
    except FileNotFoundError as exc:
        pytest.skip(str(exc))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Script:
    tokens: list[str] = field(default_factory=lambda: ["Hello", " there", "."])
    first_delay: float = 0.0
    token_delay: float = 0.0
    header_delay: float = 0.0   # like llama-server holding headers during prompt processing
    status: int = 200


@dataclass
class FakeLLM:
    url: str
    script: Script
    requests: list[dict] = field(default_factory=list)
    disconnects: int = 0
    completed: int = 0
    active: int = 0
    max_active: int = 0


@pytest_asyncio.fixture
async def fake_llm():
    state = FakeLLM(url="", script=Script())

    async def health(_):
        return web.json_response({"status": "ok"})

    async def models(_):
        return web.json_response({"data": [{"id": "fake.gguf"}]})

    async def chat(request: web.Request):
        body = await request.json()
        state.requests.append(body)
        sc = state.script
        if sc.status != 200:
            return web.json_response({"error": {"message": "loading"}}, status=sc.status)
        if sc.header_delay:
            try:
                await asyncio.sleep(sc.header_delay)
            except asyncio.CancelledError:
                state.disconnects += 1
                raise
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        state.active += 1
        state.max_active = max(state.max_active, state.active)
        try:
            await asyncio.sleep(sc.first_delay)
            for i, tok in enumerate(sc.tokens):
                if i:
                    await asyncio.sleep(sc.token_delay)
                event = {"choices": [{"index": 0, "delta": {"content": tok}, "finish_reason": None}]}
                await resp.write(b"data: " + json.dumps(event).encode() + b"\n\n")
            await resp.write(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n')
            await resp.write(b"data: [DONE]\n\n")
            state.completed += 1
        except (ConnectionResetError, asyncio.CancelledError):
            state.disconnects += 1
            raise
        finally:
            state.active -= 1
        return resp

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/chat/completions", chat)
    # handler_cancellation: like llama-server, notice a client hanging up even
    # while nothing is being written (e.g. during prompt processing).
    runner = web.AppRunner(app, shutdown_timeout=1, handler_cancellation=True)
    await runner.setup()
    port = _free_port()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    state.url = f"http://127.0.0.1:{port}"
    try:
        yield state
    finally:
        await runner.cleanup()


def _wait_health(url: str, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(url + "/health", timeout=2) as r:
                if json.loads(r.read()).get("status") == "ok":
                    return True
        except (OSError, ValueError):
            pass  # not up yet; poll again
        time.sleep(0.5)
    return False


@pytest.fixture(scope="session")
def real_llm_url():
    url = os.environ.get("SC_TEST_LLM_URL")
    if url:
        if not _wait_health(url, 10):
            pytest.skip(f"SC_TEST_LLM_URL {url} is not healthy")
        yield url
        return
    binary = os.environ.get("SC_TEST_LLAMA_SERVER")
    gguf = os.environ.get("SC_TEST_GGUF")
    if not (binary and gguf and Path(binary).is_file() and Path(gguf).is_file()):
        pytest.skip("no real llama-server: set SC_TEST_LLM_URL or SC_TEST_LLAMA_SERVER + SC_TEST_GGUF")
    port = _free_port()
    proc = subprocess.Popen(
        [binary, "-m", gguf, "--host", "127.0.0.1", "--port", str(port), "--parallel", "1",
         "-c", "8192", "--jinja", "-t", str(max(1, (os.cpu_count() or 2)))],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        if not _wait_health(url, 120):
            pytest.skip("llama-server did not become healthy")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def tokens():
    raw = {
        "jack": secrets.token_urlsafe(32),
        "partner": secrets.token_urlsafe(32),
        "voice": secrets.token_urlsafe(32),
    }
    text = f"jack chat {raw['jack']}\npartner chat {raw['partner']}\nvoice chat+speaker {raw['voice']}\n"
    return raw, parse_tokens(text)
