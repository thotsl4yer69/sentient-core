"""Loopback OpenAI-compatible completion, used only when MQTT is turned off.

The URL is restricted to loopback by GatewayConfig. Redirects are disabled so
a local server cannot hand the request to another host. Tool fields are not
forwarded.
"""

from __future__ import annotations

import json
from typing import Any

from sentient.services.gateway.errors import GatewayError

_MAX_RESPONSE_BYTES = 1024 * 1024


async def complete_via_llm(
    llm_url: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> str:
    text, _chunks = await _post(llm_url, model, messages, temperature, max_tokens, timeout, stream=False)
    return text


async def stream_via_llm(
    llm_url: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: float,
):
    _text, chunks = await _post(llm_url, model, messages, temperature, max_tokens, timeout, stream=True)
    for chunk in chunks:
        yield chunk


async def _post(
    llm_url: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: float,
    stream: bool,
) -> tuple[str, list[str]]:
    import aiohttp

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    try:
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
            async with session.post(
                f"{llm_url}/v1/chat/completions",
                json=payload,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise GatewayError(
                        502,
                        "llm_upstream",
                        "The loopback model rejected the completion.",
                        "server_error",
                    )
                if stream:
                    return "", await _read_sse(response)
                raw = await response.content.read(_MAX_RESPONSE_BYTES + 1)
    except GatewayError:
        raise
    except Exception as exc:
        raise GatewayError(502, "llm_unreachable", "The loopback model is unreachable.", "server_error") from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise GatewayError(502, "llm_too_large", "The loopback model response is too large.", "server_error")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GatewayError(502, "llm_invalid", "The loopback model returned invalid JSON.", "server_error") from exc
    text = _choice_text(body)
    if not text:
        raise GatewayError(502, "llm_empty", "The loopback model returned an empty completion.", "server_error")
    return text, []


def _choice_text(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return content if isinstance(content, str) else ""


async def _read_sse(response: Any) -> list[str]:
    chunks: list[str] = []
    buffer = b""
    total = 0
    async for piece in response.content.iter_any():
        total += len(piece)
        if total > _MAX_RESPONSE_BYTES:
            raise GatewayError(502, "llm_too_large", "The loopback model response is too large.", "server_error")
        buffer += piece
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            text = line.strip()
            if not text.startswith(b"data:"):
                continue
            data = text[5:].strip()
            if data == b"[DONE]":
                return chunks
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            for choice in event.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or {}
                content = delta.get("content") if isinstance(delta, dict) else None
                if isinstance(content, str) and content:
                    chunks.append(content)
    return chunks
