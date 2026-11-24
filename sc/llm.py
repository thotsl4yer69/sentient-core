"""Streaming client for llama.cpp ``llama-server`` (OpenAI-compatible API).

One request at a time is the norm on an 8 GB Orin (``--parallel 1``); the turn
engine guarantees that. Cancelling a turn closes the HTTP stream, and
llama-server stops generating as soon as it sees the closed connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger("sc.llm")


class LLMError(Exception):
    code = "llm_error"


class LLMUnavailable(LLMError):
    code = "llm_unavailable"


class LLMTimeout(LLMError):
    code = "llm_timeout"


class LLMCancelled(LLMError):
    code = "cancelled"


@dataclass
class StreamStats:
    started: float = 0.0
    first_token_at: float | None = None
    finished: float | None = None
    tokens: int = 0
    finish_reason: str | None = None

    @property
    def first_token_s(self) -> float | None:
        return None if self.first_token_at is None else self.first_token_at - self.started

    @property
    def total_s(self) -> float | None:
        return None if self.finished is None else self.finished - self.started


class LLMClient:
    def __init__(self, base_url: str, model: str, *, connect_timeout_s: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.connect_timeout_s = connect_timeout_s
        self._session: aiohttp.ClientSession | None = None

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, connect=self.connect_timeout_s, sock_connect=self.connect_timeout_s)
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def health(self) -> dict[str, Any]:
        """``{"ok": bool, "status": str, "model": str|None}`` — never raises."""
        try:
            http = await self._http()
            async with http.get(f"{self.base_url}/health", timeout=aiohttp.ClientTimeout(total=3)) as r:
                body = await r.json(content_type=None)
                status = body.get("status", str(r.status)) if isinstance(body, dict) else str(r.status)
                ok = r.status == 200 and status == "ok"
            model = None
            if ok:
                async with http.get(f"{self.base_url}/v1/models", timeout=aiohttp.ClientTimeout(total=3)) as r:
                    data = await r.json(content_type=None)
                    items = data.get("data") if isinstance(data, dict) else None
                    if items:
                        model = items[0].get("id")
            return {"ok": ok, "status": status, "model": model}
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            return {"ok": False, "status": f"unreachable: {type(exc).__name__}", "model": None}

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float,
        first_token_s: float,
        total_s: float,
        cancel: asyncio.Event,
        stats: StreamStats | None = None,
    ) -> AsyncIterator[str]:
        """Yield text deltas. Raises LLMUnavailable, LLMTimeout or LLMCancelled."""
        stats = stats if stats is not None else StreamStats()
        stats.started = time.monotonic()
        deadline = stats.started + total_s
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "cache_prompt": True,
        }
        http = await self._http()
        # llama-server may hold the response headers while it processes the
        # prompt, so race the request itself against cancellation too.
        post = asyncio.ensure_future(http.post(f"{self.base_url}/v1/chat/completions", json=payload))
        waiter = asyncio.ensure_future(cancel.wait())
        try:
            done, _ = await asyncio.wait({post, waiter}, timeout=min(first_token_s, total_s),
                                         return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiter.cancel()
        if post not in done:
            post.cancel()  # aborts the connection; llama-server drops the task
            try:
                await post
            except (asyncio.CancelledError, aiohttp.ClientError):
                pass
            if cancel.is_set():
                raise LLMCancelled("turn cancelled before the model answered")
            raise LLMTimeout("model server did not accept the request in time")
        try:
            response = post.result()
        except aiohttp.ClientError as exc:
            raise LLMUnavailable(f"model server unreachable: {exc}") from exc
        try:
            if response.status == 503:
                raise LLMUnavailable("model server is loading or busy (HTTP 503)")
            if response.status != 200:
                text = (await response.text())[:300]
                raise LLMError(f"model server HTTP {response.status}: {text}")
            buffer = b""
            while True:
                if cancel.is_set():
                    raise LLMCancelled("turn cancelled")
                now = time.monotonic()
                limit = (stats.started + first_token_s) if stats.first_token_at is None else deadline
                limit = min(limit, deadline)
                remaining = limit - now
                if remaining <= 0:
                    raise LLMTimeout("first token too slow" if stats.first_token_at is None else "reply too slow")
                read = asyncio.ensure_future(response.content.readany())
                waiter = asyncio.ensure_future(cancel.wait())
                done, _ = await asyncio.wait({read, waiter}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
                if waiter in done:
                    read.cancel()
                    raise LLMCancelled("turn cancelled")
                waiter.cancel()
                if read not in done:
                    read.cancel()
                    raise LLMTimeout("first token too slow" if stats.first_token_at is None else "reply too slow")
                chunk = read.result()
                if not chunk:
                    break  # server closed the stream
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if data == b"[DONE]":
                        stats.finished = time.monotonic()
                        return
                    try:
                        event = json.loads(data)
                    except ValueError:
                        logger.warning("unparseable SSE line from model server: %r", data[:120])
                        continue
                    if "error" in event:
                        raise LLMError(f"model server error: {event['error']}")
                    for choice in event.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if choice.get("finish_reason"):
                            stats.finish_reason = choice["finish_reason"]
                        if delta:
                            if stats.first_token_at is None:
                                stats.first_token_at = time.monotonic()
                            stats.tokens += 1
                            yield delta
            stats.finished = time.monotonic()
        except aiohttp.ClientError as exc:
            raise LLMUnavailable(f"model server connection lost: {exc}") from exc
        finally:
            # Closing (not releasing) aborts the connection so llama-server
            # stops generating for a cancelled or timed-out turn.
            response.close()
