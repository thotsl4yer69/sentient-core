"""OpenAI-compatible local inference adapter.

Speaks the chat-completions API used by both llama.cpp server and Ollama, so
the Gate B2 benchmark can choose the engine without a second client. Callers
may still pass a legacy Ollama model name: llama.cpp has one loaded GGUF and
that id is resolved from ``/v1/models``. Ollama is asked for the requested name.

Limits from the voice-turn rules:

- voice: start within 2 s, generate at most 20 s or 256 tokens
- chat: start within 30 s, generate at most 60 s
- background: start within 1 h, generate at most 180 s

A voice request cancels any non-voice generation already holding the single
slot by closing that stream. The cancelled call is recorded for requeue and
does not count as an attempt. Nothing here loads a second model: failover
runs only when the fallback endpoint already reports a resident model on
``/api/ps``.
"""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

log = logging.getLogger(__name__)

TURN_HEADER = "X-Turn-Id"
PRIORITY_HEADER = "X-Sc-Priority"

PRIORITY_LIMITS = {
    "voice": {"max_duration": 20.0, "max_tokens": 256, "start_deadline": 2.0},
    "chat": {"max_duration": 60.0, "max_tokens": None, "start_deadline": 30.0},
    "background": {"max_duration": 180.0, "max_tokens": None, "start_deadline": 3600.0},
}

_TERMINAL_NO_FAILOVER = ("cancelled", "deadline", "duration")


class GenerationCancelled(Exception):
    """The caller closed this turn's stream."""


class DeadlineExceeded(Exception):
    """The request was still waiting for the single generation slot."""


class DurationExceeded(Exception):
    """Generation ran past its max duration."""


@dataclass
class GenerationOutcome:
    """Result of one completion, including turns that did not produce text."""

    status: str
    text: str = ""
    turn_id: str = ""
    priority: str = ""
    count_attempt: bool = True
    requeue: bool = False
    error: str = ""
    request: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _Flight:
    turn_id: str
    priority: str
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)
    response: Optional[aiohttp.ClientResponse] = None
    opener: Optional[asyncio.Task] = None
    cm: Any = None
    exited: bool = False


class LocalOpenAIClient:
    def __init__(self, host: str = "http://127.0.0.1:18080", backend: str = "llama.cpp"):
        self.host = host.rstrip("/")
        self.backend = backend
        self._session: Optional[aiohttp.ClientSession] = None
        self._model: Optional[str] = None
        self._flights: Dict[str, _Flight] = {}
        self._holder: Optional[_Flight] = None
        self._slot_lock = asyncio.Lock()
        self.last_outcome: Optional[GenerationOutcome] = None
        self.outcomes: Dict[str, GenerationOutcome] = {}
        self.requeue: List[GenerationOutcome] = []

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _detect_backend(self) -> str:
        if self.backend != "auto":
            return self.backend
        session = await self._get_session()
        try:
            async with session.get(
                f"{self.host}/api/tags",
                timeout=aiohttp.ClientTimeout(total=2),
            ) as response:
                if response.status == 200:
                    self.backend = "ollama"
                    return self.backend
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        self.backend = "llama.cpp"
        return self.backend

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
                raise RuntimeError("Local inference server returned no loaded model")
            self._model = models[0]["id"]
        return self._model

    async def _model_for_request(self, requested: str) -> str:
        kind = await self._detect_backend()
        if kind == "ollama" and requested:
            return requested
        return await self._resolve_model()

    def _limits(
        self,
        priority: Optional[str],
        timeout: float,
        max_tokens: int,
        max_duration: Optional[float],
        deadline: Optional[float],
    ):
        spec = PRIORITY_LIMITS.get(priority or "", {})
        if priority:
            duration = spec["max_duration"] if max_duration is None else max_duration
            start_deadline = spec["start_deadline"] if deadline is None else deadline
            token_cap = spec.get("max_tokens")
            if token_cap is not None:
                max_tokens = min(max_tokens, token_cap)
        else:
            duration = timeout if max_duration is None else max_duration
            start_deadline = deadline
        return duration, start_deadline, max_tokens

    async def cancel(self, turn_id: str) -> bool:
        """Close the in-flight response for ``turn_id``, if any."""
        flight = self._flights.get(turn_id)
        if flight is None:
            return False
        flight.cancel.set()
        response = flight.response
        if response is not None and not response.closed:
            response.close()
        opener = flight.opener
        if opener is not None and not opener.done():
            opener.cancel()
        return True

    async def cancel_non_voice(self) -> List[str]:
        """Cancel every in-flight generation that is not a voice turn."""
        cancelled = []
        for turn_id, flight in list(self._flights.items()):
            if flight.priority == "voice":
                continue
            if await self.cancel(turn_id):
                cancelled.append(turn_id)
        return cancelled

    def drain_requeue(self) -> List[GenerationOutcome]:
        """Return cancelled turns the caller should submit again.

        ``count_attempt`` is false on each of them. Draining clears the buffer
        so a retry is not issued twice.
        """
        pending = list(self.requeue)
        self.requeue.clear()
        return pending

    async def _acquire_slot(self, flight: _Flight, start_deadline: Optional[float]) -> None:
        started = time.monotonic()
        while True:
            if flight.cancel.is_set():
                raise GenerationCancelled()
            async with self._slot_lock:
                holder = self._holder
                if holder is None:
                    self._holder = flight
                    return
            if flight.priority == "voice" and holder.priority != "voice":
                await self.cancel(holder.turn_id)
            if start_deadline is not None and (time.monotonic() - started) > start_deadline:
                raise DeadlineExceeded()
            try:
                await asyncio.wait_for(holder.released.wait(), timeout=0.05)
            except asyncio.TimeoutError:
                pass

    def _release_slot(self, flight: _Flight) -> None:
        if self._holder is flight:
            self._holder = None
        flight.released.set()

    async def _stop_task(self, task: asyncio.Task) -> None:
        if task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _close_cm(self, flight: _Flight) -> None:
        cm = flight.cm
        if cm is None or flight.exited:
            return
        flight.exited = True
        try:
            await cm.__aexit__(None, None, None)
        except Exception:
            log.debug("Closing inference response raised", exc_info=True)

    async def _abort_open(self, flight: _Flight) -> None:
        opener = flight.opener
        if opener is not None and not opener.done():
            opener.cancel()
            try:
                await opener
            except (asyncio.CancelledError, Exception):
                pass
        elif opener is not None and opener.done() and not opener.cancelled():
            try:
                flight.response = opener.result()
            except Exception:
                pass
        await self._close_cm(flight)

    async def _open(self, session, payload, headers, flight, header_timeout):
        """Wait until response headers arrive, bounded by the generation budget.

        Streaming servers send headers when generation starts. A non-streaming
        server sends them with the finished body, so this same budget is the
        non-streaming max duration.
        """
        total = header_timeout + 5
        timeout = aiohttp.ClientTimeout(
            total=total, sock_connect=10, sock_read=max(header_timeout, 1)
        )
        cm = session.post(
            f"{self.host}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        flight.cm = cm
        opener = asyncio.create_task(cm.__aenter__())
        flight.opener = opener
        cancel_task = asyncio.create_task(flight.cancel.wait())
        try:
            done, _pending = await asyncio.wait(
                {opener, cancel_task},
                timeout=header_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            await self._abort_open(flight)
            raise
        if not done or (flight.cancel.is_set() and opener not in done):
            await self._stop_task(cancel_task)
            await self._abort_open(flight)
            if flight.cancel.is_set():
                raise GenerationCancelled()
            raise DurationExceeded()
        await self._stop_task(cancel_task)
        try:
            response = await opener
        except asyncio.CancelledError:
            await self._close_cm(flight)
            raise GenerationCancelled()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            await self._close_cm(flight)
            if flight.cancel.is_set():
                raise GenerationCancelled() from exc
            if isinstance(exc, asyncio.TimeoutError):
                raise DurationExceeded() from exc
            raise
        flight.response = response
        if flight.cancel.is_set():
            response.close()
            await self._close_cm(flight)
            raise GenerationCancelled()
        if response.status >= 400:
            detail = await response.text()
            await self._close_cm(flight)
            raise RuntimeError(f"Inference HTTP {response.status}: {detail[:300]}")
        return response

    async def _read_json(self, response, flight, max_duration):
        reader = asyncio.create_task(response.json(content_type=None))
        cancel_task = asyncio.create_task(flight.cancel.wait())
        try:
            done, _pending = await asyncio.wait(
                {reader, cancel_task},
                timeout=max_duration,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if reader in done and not reader.cancelled():
                await self._stop_task(cancel_task)
                return reader.result()
            response.close()
            await self._stop_task(reader)
            await self._stop_task(cancel_task)
            if flight.cancel.is_set():
                raise GenerationCancelled()
            raise DurationExceeded()
        finally:
            await self._stop_task(cancel_task)

    async def _iter_sse(self, response, flight, max_duration) -> AsyncIterator[str]:
        buffer = b""
        end_at = time.monotonic() + max_duration
        while True:
            if flight.cancel.is_set():
                response.close()
                raise GenerationCancelled()
            remaining = end_at - time.monotonic()
            if remaining <= 0:
                response.close()
                raise DurationExceeded()
            try:
                chunk = await asyncio.wait_for(response.content.read(1024), timeout=remaining)
            except asyncio.TimeoutError as exc:
                response.close()
                if flight.cancel.is_set():
                    raise GenerationCancelled() from exc
                raise DurationExceeded() from exc
            except aiohttp.ClientError as exc:
                if flight.cancel.is_set():
                    raise GenerationCancelled() from exc
                raise
            if not chunk:
                if flight.cancel.is_set():
                    raise GenerationCancelled()
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                token = self._token_from_sse_line(line)
                if token is None:
                    continue
                if token == "":
                    return
                yield token

    @staticmethod
    def _token_from_sse_line(line: bytes) -> Optional[str]:
        """Return a content token, ``''`` at end of stream, or None to skip."""
        import json

        line = line.strip()
        if not line.startswith(b"data:"):
            return None
        body = line[5:].strip()
        if body == b"[DONE]":
            return ""
        try:
            event = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            log.warning("Skipping malformed SSE data")
            return None
        parts = []
        for choice in event.get("choices", []):
            delta = choice.get("delta", {})
            content = delta.get("content") or ""
            if isinstance(content, str) and content:
                parts.append(content)
        if not parts:
            return None
        return "".join(parts)

    def _remember(self, outcome: GenerationOutcome) -> None:
        self.last_outcome = outcome
        if outcome.turn_id:
            self.outcomes[outcome.turn_id] = outcome
        if outcome.requeue:
            self.requeue.append(outcome)
            log.info(
                "Turn %s cancelled before completion; requeue without counting an attempt",
                outcome.turn_id,
            )

    def _finish(self, outcome: GenerationOutcome, status: str, error: str = "") -> None:
        outcome.status = status
        outcome.error = error
        if status == "cancelled":
            outcome.count_attempt = False
            outcome.requeue = True
        self._remember(outcome)

    async def _run(
        self,
        kind: str,
        payload: Dict[str, Any],
        headers,
        flight,
        start_deadline,
        max_duration,
        request,
        requested_model: str,
    ):
        outcome = GenerationOutcome(
            status="error",
            turn_id=flight.turn_id,
            priority=flight.priority,
            request=request,
        )
        self._flights[flight.turn_id] = flight
        try:
            await self._acquire_slot(flight, start_deadline)
            payload["model"] = await self._model_for_request(requested_model)
            request["model"] = payload["model"]
            session = await self._get_session()
            started = time.monotonic()
            response = await self._open(session, payload, headers, flight, max_duration)
            remaining = max_duration - (time.monotonic() - started)
            if remaining <= 0:
                response.close()
                await self._close_cm(flight)
                raise DurationExceeded()
            if payload.get("stream"):
                async for token in self._iter_sse(response, flight, remaining):
                    outcome.text += token
                    yield token
            else:
                result = await self._read_json(response, flight, remaining)
                choices = result.get("choices", [])
                if not choices:
                    raise RuntimeError("Inference server returned no choices")
                outcome.text = (choices[0].get("message", {}).get("content") or "").strip()
            await self._close_cm(flight)
            self._finish(outcome, "ok" if outcome.text else "empty")
        except GenerationCancelled:
            await self._close_cm(flight)
            self._finish(outcome, "cancelled")
        except DeadlineExceeded:
            await self._close_cm(flight)
            self._finish(outcome, "deadline", "start deadline exceeded")
            log.warning("Turn %s missed its start deadline", flight.turn_id)
        except DurationExceeded:
            await self._close_cm(flight)
            self._finish(outcome, "duration", "max generation duration exceeded")
            log.warning("Turn %s exceeded max generation duration", flight.turn_id)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RuntimeError) as exc:
            await self._close_cm(flight)
            if flight.cancel.is_set():
                self._finish(outcome, "cancelled")
            else:
                self._finish(outcome, "error", str(exc))
                log.error("Local model %s failed: %s", kind, exc)
        except Exception as exc:
            await self._close_cm(flight)
            if flight.cancel.is_set():
                self._finish(outcome, "cancelled")
            else:
                self._finish(outcome, "error", str(exc))
                log.error("Local model %s failed: %s", kind, exc)
        finally:
            self._release_slot(flight)
            self._flights.pop(flight.turn_id, None)

    def _prepare(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        timeout: float,
        stop: Optional[List[str]],
        stream: bool,
        turn_id: Optional[str],
        priority: Optional[str],
        max_duration: Optional[float],
        deadline: Optional[float],
    ):
        duration, start_deadline, max_tokens = self._limits(
            priority, timeout, max_tokens, max_duration, deadline
        )
        turn = turn_id or str(uuid.uuid4())
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if stop:
            payload["stop"] = stop
        headers = {TURN_HEADER: turn}
        if priority:
            headers[PRIORITY_HEADER] = priority
        flight = _Flight(turn_id=turn, priority=priority or "chat")
        request = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "stop": stop,
            "stream": stream,
            "turn_id": turn,
            "priority": priority,
            "max_duration": duration,
            "deadline": start_deadline,
        }
        return payload, headers, flight, start_deadline, duration, request

    async def generate(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 256,
        timeout: float = 30.0,
        stop: Optional[List[str]] = None,
        turn_id: Optional[str] = None,
        priority: Optional[str] = None,
        max_duration: Optional[float] = None,
        deadline: Optional[float] = None,
    ) -> str:
        messages = ([{"role": "system", "content": system}] if system else [])
        messages.append({"role": "user", "content": prompt})
        payload, headers, flight, start_deadline, duration, request = self._prepare(
            model, messages, temperature, max_tokens, timeout, stop, False,
            turn_id, priority, max_duration, deadline,
        )
        async for _token in self._run(
            "completion", payload, headers, flight, start_deadline, duration, request, model
        ):
            pass
        return (self.last_outcome.text if self.last_outcome else "").strip()

    async def chat_stream(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 256,
        timeout: float = 120.0,
        stop: Optional[List[str]] = None,
        turn_id: Optional[str] = None,
        priority: Optional[str] = None,
        max_duration: Optional[float] = None,
        deadline: Optional[float] = None,
    ) -> AsyncIterator[str]:
        payload, headers, flight, start_deadline, duration, request = self._prepare(
            model, messages, temperature, max_tokens, timeout, stop, True,
            turn_id, priority, max_duration, deadline,
        )
        async for token in self._run(
            "stream", payload, headers, flight, start_deadline, duration, request, model
        ):
            yield token

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
        turn_id: Optional[str] = None,
        priority: Optional[str] = None,
        max_duration: Optional[float] = None,
        deadline: Optional[float] = None,
    ) -> AsyncIterator[str]:
        del on_thinking_start, on_thinking_end
        messages = ([{"role": "system", "content": system}] if system else [])
        messages.append({"role": "user", "content": prompt})
        async for token in self.chat_stream(
            model, messages, temperature, max_tokens, timeout, stop,
            turn_id, priority, max_duration, deadline,
        ):
            yield token

    async def close(self):
        for turn_id in list(self._flights):
            await self.cancel(turn_id)
        if self._session and not self._session.closed:
            await self._session.close()


class FailoverClient:
    """Prefer the primary endpoint. Fall back only when a model is already loaded.

    ``GET /api/ps`` must list a resident model. An empty or unreachable
    fallback is left alone so a failure cannot pull a second runtime into the
    8 GB unified pool. Streaming still does not switch after the first token.
    Cancelled, deadline, and duration outcomes are not retried on the fallback.
    """

    def __init__(self, primary: LocalOpenAIClient, fallback):
        self.primary = primary
        self.fallback = fallback
        self.last_outcome: Optional[GenerationOutcome] = None
        self.requeue = self.primary.requeue

    async def _fallback_loaded(self) -> bool:
        host = getattr(self.fallback, "host", None)
        if not host:
            return False
        timeout = aiohttp.ClientTimeout(total=2)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{str(host).rstrip('/')}/api/ps") as response:
                    if response.status != 200:
                        return False
                    payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return False
        models = payload.get("models") if isinstance(payload, dict) else None
        return bool(models)

    def _blocked(self, outcome: Optional[GenerationOutcome]) -> bool:
        return outcome is not None and outcome.status in _TERMINAL_NO_FAILOVER

    async def generate(self, *args, **kwargs) -> str:
        result = await self.primary.generate(*args, **kwargs)
        self.last_outcome = self.primary.last_outcome
        if result:
            return result
        if self._blocked(self.last_outcome):
            return ""
        if not await self._fallback_loaded():
            log.warning("Fallback has no resident model; not loading a second runtime")
            return ""
        log.warning("Primary model unavailable; retrying on the already-loaded fallback")
        result = await self.fallback.generate(*args, **kwargs)
        self.last_outcome = getattr(self.fallback, "last_outcome", self.last_outcome)
        return result

    async def chat_stream(self, *args, **kwargs):
        emitted = False
        async for token in self.primary.chat_stream(*args, **kwargs):
            emitted = True
            yield token
        self.last_outcome = self.primary.last_outcome
        if emitted or self._blocked(self.last_outcome):
            return
        if not await self._fallback_loaded():
            log.warning("Fallback has no resident model; not loading a second runtime")
            return
        log.warning("Primary streaming unavailable; retrying on the already-loaded fallback")
        async for token in self.fallback.chat_stream(*args, **kwargs):
            yield token
        self.last_outcome = getattr(self.fallback, "last_outcome", self.last_outcome)

    async def generate_stream(self, *args, **kwargs):
        emitted = False
        async for token in self.primary.generate_stream(*args, **kwargs):
            emitted = True
            yield token
        self.last_outcome = self.primary.last_outcome
        if emitted or self._blocked(self.last_outcome):
            return
        if not await self._fallback_loaded():
            log.warning("Fallback has no resident model; not loading a second runtime")
            return
        log.warning("Primary streaming unavailable; retrying on the already-loaded fallback")
        async for token in self.fallback.generate_stream(*args, **kwargs):
            yield token
        self.last_outcome = getattr(self.fallback, "last_outcome", self.last_outcome)

    async def cancel(self, turn_id: str) -> bool:
        hit = await self.primary.cancel(turn_id)
        fallback_cancel = getattr(self.fallback, "cancel", None)
        if fallback_cancel is not None:
            hit = await fallback_cancel(turn_id) or hit
        return hit

    def drain_requeue(self) -> List[GenerationOutcome]:
        pending = self.primary.drain_requeue()
        extra = getattr(self.fallback, "drain_requeue", None)
        if extra is not None:
            pending.extend(extra())
        return pending

    async def close(self):
        await self.primary.close()
        await self.fallback.close()
