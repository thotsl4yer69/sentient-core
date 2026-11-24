"""Streams a voice turn to sc-core and cancels it on barge-in."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import aiohttp

logger = logging.getLogger("sc.voice.core")


class CoreUnavailable(Exception):
    pass


class CoreClient:
    def __init__(self, url: str, token: str, speaker: str, participants: tuple[str, ...]):
        self.url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self.speaker = speaker
        self.participants = tuple(participants) or (speaker,)
        self._http: aiohttp.ClientSession | None = None
        self.turn_id: str | None = None

    async def _session(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, connect=3, sock_read=40), headers=self._headers
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.closed:
            await self._http.close()

    async def stream(self, text: str) -> AsyncIterator[str]:
        body = {
            "messages": [{"role": "user", "content": text}],
            "stream": True,
            "sc_kind": "voice",
            "sc_speaker": self.speaker,
            "sc_participants": list(self.participants),
        }
        self.turn_id = None
        try:
            http = await self._session()
            resp = await http.post(f"{self.url}/v1/chat/completions", json=body)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise CoreUnavailable(f"sc-core unreachable: {exc}") from exc
        try:
            if resp.status != 200:
                raise CoreUnavailable(f"sc-core HTTP {resp.status}: {(await resp.text())[:200]}")
            self.turn_id = resp.headers.get("X-SC-Turn-Id") or None
            async for raw in resp.content:
                line = raw.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    return
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if "error" in event:
                    logger.warning("sc-core stream error: %s", event["error"])
                    return
                for choice in event.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        yield piece
        except aiohttp.ClientError as exc:
            raise CoreUnavailable(f"sc-core stream broke: {exc}") from exc
        finally:
            resp.close()

    async def cancel(self) -> None:
        if not self.turn_id:
            return
        try:
            http = await self._session()
            async with http.post(f"{self.url}/v1/turns/{self.turn_id}/cancel",
                                 timeout=aiohttp.ClientTimeout(total=3)) as r:
                await r.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("could not cancel turn %s: %s", self.turn_id, exc)
