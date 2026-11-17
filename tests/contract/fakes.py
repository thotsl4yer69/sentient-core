"""In-process stand-in for the contemplation adapter's public methods."""

from __future__ import annotations

import asyncio
from typing import Any


class ScriptedLLM:
    """Records ``chat_stream`` / ``generate`` calls and yields a scripted reply.

    A script step is a string token, ``("sleep", seconds)``, or ``("block", event)``.
    """

    def __init__(self, default: list[Any] | None = None):
        self.default = default if default is not None else ["Ready."]
        self.scripts: dict[str, list[Any]] = {}
        self.calls: list[dict[str, Any]] = []
        self.closed: list[str] = []

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 256,
        timeout: float = 120.0,
        stop: list[str] | None = None,
    ):
        user = messages[-1]["content"]
        self.calls.append({
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "stop": stop,
            "user": user,
        })
        try:
            for step in self.scripts.get(user, self.default):
                if isinstance(step, tuple) and step[0] == "sleep":
                    await asyncio.sleep(step[1])
                elif isinstance(step, tuple) and step[0] == "block":
                    await step[1].wait()
                else:
                    yield str(step)
        finally:
            self.closed.append(user)

    async def generate(
        self,
        model: str,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 256,
        timeout: float = 30.0,
        stop: list[str] | None = None,
    ) -> str:
        self.calls.append({
            "op": "generate",
            "model": model,
            "prompt": prompt,
            "system": system,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "stop": stop,
        })
        return "generated"

    async def close(self) -> None:
        self.closed.append("close")
