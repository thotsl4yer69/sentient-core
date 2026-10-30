"""OpenAI-compatible local inference adapter for llama.cpp on Jetson.

Implements the method contract consumed by the existing contemplation engine.
Uses aiohttp already required by Sentient Core; no cloud SDK or API key.
The model name is resolved from llama.cpp's /v1/models once per session,
so callers can continue supplying their legacy Ollama model argument.
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import aiohttp

log = logging.getLogger(__name__)


class LocalOpenAIClient:
    def __init__(self, host: str = "http://127.0.0.1:18080"):
        self.host = host.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
        self._model: Optional[str] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _resolve_model(self) -> str:
        if self._model:
            return self._model
        session = await self._get_session()
        async with session.get(
            f"{self.host}/v1/models",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            response.raise_for_status()
            models = (await response.json()).get("data", [])
            if not models or not models[0].get("id"):
                raise RuntimeError("Local llama.cpp server returned no loaded model")
            self._model = models[0]["id"]
        return self._model

    async def generate(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 256,
        timeout: float = 30.0,
        stop: Optional[List[str]] = None,
    ) -> str:
        messages = ([{"role": "system", "content": system}] if system else [])
        messages.append({"role": "user", "content": prompt})
        try:
            session = await self._get_session()
            payload: Dict[str, Any] = {
                "model": await self._resolve_model(),
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
            if stop:
                payload["stop"] = stop
            async with session.post(
                f"{self.host}/v1/chat/completions",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                response.raise_for_status()
                result = await response.json()
                choices = result.get("choices", [])
                if not choices:
                    raise RuntimeError("llama.cpp returned no choices")
                return (choices[0].get("message", {}).get("content") or "").strip()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RuntimeError) as exc:
            log.error("Local model completion failed: %s", exc)
            return ""

    async def chat_stream(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 256,
        timeout: float = 120.0,
        stop: Optional[List[str]] = None,
    ):
        try:
            session = await self._get_session()
            payload: Dict[str, Any] = {
                "model": await self._resolve_model(),
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            }
            if stop:
                payload["stop"] = stop
            async with session.post(
                f"{self.host}/v1/chat/completions",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                response.raise_for_status()
                buffer = b""
                async for chunk in response.content.iter_any():
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.strip()
                        if not line.startswith(b"data:"):
                            continue
                        body = line[5:].strip()
                        if body == b"[DONE]":
                            return
                        try:
                            event = json.loads(body)
                        except (ValueError, UnicodeDecodeError):
                            log.warning("Skipping malformed SSE data")
                            continue
                        for choice in event.get("choices", []):
                            delta = choice.get("delta", {})
                            content = delta.get("content") or ""
                            if isinstance(content, str) and content:
                                yield content
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.error("Local model streaming failed: %s", exc)
            return

    async def generate_stream(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 256,
        timeout: float = 120.0,
        on_thinking_start=None,
        on_thinking_end=None,
        stop: Optional[List[str]] = None,
    ):
        messages = ([{"role": "system", "content": system}] if system else [])
        messages.append({"role": "user", "content": prompt})
        async for token in self.chat_stream(
            model, messages, temperature, max_tokens, timeout, stop
        ):
            yield token

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class FailoverClient:
    """Prefer local llama.cpp and retry the prior Ollama engine on a failed turn.

    A streaming fallback happens only before the first output token; restarting
    after partial output would duplicate or corrupt a spoken response.
    """

    def __init__(self, primary: LocalOpenAIClient, fallback):
        self.primary = primary
        self.fallback = fallback

    async def generate(self, *args, **kwargs):
        result = await self.primary.generate(*args, **kwargs)
        if result:
            return result
        log.warning("Local model unavailable; retrying on existing Ollama")
        return await self.fallback.generate(*args, **kwargs)

    async def chat_stream(self, *args, **kwargs):
        emitted = False
        async for token in self.primary.chat_stream(*args, **kwargs):
            emitted = True
            yield token
        if not emitted:
            log.warning("Local streaming unavailable; retrying on existing Ollama")
            async for token in self.fallback.chat_stream(*args, **kwargs):
                yield token

    async def generate_stream(self, *args, **kwargs):
        emitted = False
        async for token in self.primary.generate_stream(*args, **kwargs):
            emitted = True
            yield token
        if not emitted:
            log.warning("Local streaming unavailable; retrying on existing Ollama")
            async for token in self.fallback.generate_stream(*args, **kwargs):
                yield token

    async def close(self):
        await self.primary.close()
        await self.fallback.close()
