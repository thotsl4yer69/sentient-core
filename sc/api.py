"""OpenAI-compatible HTTP API for AIRI and sc-voice.

AIRI: Settings → Providers → Chat → "OpenAI Compatible", base URL
``http://<nanob>:8770/v1``, API key = that person's token. The token decides who
is talking. Client system prompts are ignored: the persona lives here.

sc-voice: same endpoint with a ``speaker``-scoped token plus extension fields
``sc_kind: "voice"``, ``sc_speaker`` and ``sc_participants``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.cors import CORSMiddleware

from sc.auth import AuthError, Principal, TokenStore, authenticate
from sc.brain import Brain, TurnRequest
from sc.config import Settings
from sc.llm import LLMCancelled, LLMError, LLMTimeout, LLMUnavailable
from sc.turns import TurnRejected

logger = logging.getLogger("sc.api")

MAX_BODY = 256 * 1024
MAX_MESSAGE_CHARS = 16000
MAX_MESSAGES = 60


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, kind: str = "invalid_request_error"):
        super().__init__(message)
        self.status, self.code, self.message, self.kind = status, code, message, kind

    def body(self) -> dict[str, Any]:
        return {"error": {"message": self.message, "type": self.kind, "code": self.code}}


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: str
    content: Any = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    user: str | None = None
    metadata: dict[str, Any] | None = None
    sc_kind: str | None = None
    sc_speaker: str | None = None
    sc_participants: list[str] | None = None


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
            else:
                raise ApiError(400, "text_only", "Only text message content is supported.")
        return "\n".join(parts)
    raise ApiError(400, "text_only", "Only text message content is supported.")


def to_turn_request(body: ChatRequest, who: Principal, known_users: frozenset[str]) -> TurnRequest:
    if len(body.messages) > MAX_MESSAGES:
        raise ApiError(400, "too_many_messages", f"At most {MAX_MESSAGES} messages per request.")
    convo: list[dict[str, str]] = []
    for m in body.messages:
        if m.role not in ("system", "user", "assistant", "tool", "developer"):
            raise ApiError(400, "invalid_role", f"Unknown role {m.role!r}.")
        text = _text(m.content)
        if len(text) > MAX_MESSAGE_CHARS:
            raise ApiError(400, "message_too_large", f"A message is over {MAX_MESSAGE_CHARS} characters.")
        if m.role in ("user", "assistant") and text.strip():
            convo.append({"role": m.role, "content": text})
    if not convo or convo[-1]["role"] != "user":
        raise ApiError(400, "no_user_message", "The last message must be a user message.")

    meta = body.metadata or {}
    kind = body.sc_kind or meta.get("sc_kind") or "chat"
    if kind not in ("chat", "voice"):
        raise ApiError(400, "invalid_kind", "sc_kind must be 'chat' or 'voice'.")
    speaker_override = body.sc_speaker or meta.get("sc_speaker")
    if (kind == "voice" or speaker_override) and not who.can("speaker"):
        raise ApiError(403, "speaker_scope_required", "This token may only speak as its own user.", "permission_error")
    speaker = (speaker_override or who.user).strip().lower()
    participants = body.sc_participants or meta.get("sc_participants") or [speaker]
    if not isinstance(participants, list) or not all(isinstance(p, str) for p in participants):
        raise ApiError(400, "invalid_participants", "sc_participants must be a list of user names.")
    everyone = {p.strip().lower() for p in participants} | {speaker}
    allowed = known_users | {"guest"}
    unknown = everyone - allowed
    if unknown:
        raise ApiError(400, "unknown_participants", f"Unknown people: {sorted(unknown)}.")
    prior = convo[:-1]
    return TurnRequest(
        kind=kind,
        speaker=speaker,
        participants=tuple(sorted(everyone)),
        text=convo[-1]["content"],
        client_history=prior if prior else None,
    )


def _status_for(exc: Exception) -> ApiError:
    if isinstance(exc, TurnRejected):
        return ApiError(429, "busy", "Cortana is busy with another reply. Try again shortly.", "rate_limit_error")
    if isinstance(exc, LLMUnavailable):
        return ApiError(503, "llm_unavailable", "The local language model isn't running.", "server_error")
    if isinstance(exc, LLMTimeout):
        return ApiError(504, "llm_timeout", "The local language model took too long.", "server_error")
    if isinstance(exc, LLMError):
        return ApiError(502, "llm_error", "The local language model returned an error.", "server_error")
    return ApiError(500, "internal_error", "Internal error.", "server_error")


class ClientGone(Exception):
    """The HTTP client disconnected before the reply started."""


async def _until_first(request: Request, step: asyncio.Future, turn) -> Any:
    """Await ``step`` (the first delta, or a whole non-stream reply) but stop the
    turn as soon as the client goes away, so an abandoned turn (e.g. a
    barge-in while Cortana is still thinking) frees the model at once."""
    while True:
        done, _ = await asyncio.wait({step}, timeout=0.1)
        if done:
            return step.result()
        if await request.is_disconnected():
            turn.stop("client-gone")
            try:
                await step
            except (StopAsyncIteration, LLMCancelled, LLMError, TurnRejected):
                pass
            raise ClientGone


def create_app(settings: Settings, brain: Brain, tokens: TokenStore) -> FastAPI:
    app = FastAPI(title="Sentient Core", version="3", docs_url=None, redoc_url=None, openapi_url=None)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            expose_headers=["X-SC-Turn-Id"],
            max_age=600,
        )

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(AuthError)
    async def _auth_error(_: Request, exc: AuthError):
        return JSONResponse(
            {"error": {"message": exc.message, "type": "authentication_error", "code": exc.code}},
            status_code=exc.status,
            headers={"WWW-Authenticate": "Bearer"},
        )

    def principal(request: Request) -> Principal:
        return authenticate(tokens, request.headers.get("authorization"))

    @app.get("/healthz")
    async def healthz(request: Request):
        health = await brain.health()
        code = 200 if health["status"] == "ok" else 503
        authed = False
        if request.headers.get("authorization"):
            principal(request)
            authed = True
        body = health if (authed or not settings.lan_exposed) else {"status": health["status"]}
        return JSONResponse(body, status_code=code)

    @app.get("/v1/models")
    async def models(request: Request):
        principal(request)
        return {"object": "list", "data": [{"id": settings.assistant_name.lower(), "object": "model",
                                            "created": 0, "owned_by": "sentient-core"}]}

    @app.post("/v1/turns/{turn_id}/cancel")
    async def cancel(turn_id: str, request: Request):
        who = principal(request)
        turn = brain.engine.get(turn_id)
        if turn is None:
            raise ApiError(404, "unknown_turn", "No such turn.")
        if turn.user != who.user and not (who.can("speaker") or who.can("admin")):
            raise ApiError(403, "not_your_turn", "You can only cancel your own turns.", "permission_error")
        return {"cancelled": brain.engine.cancel(turn_id, "client"), "turn_id": turn_id}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        who = principal(request)
        if not (who.can("chat") or who.can("speaker")):
            raise ApiError(403, "chat_scope_required", "This token can't chat.", "permission_error")
        length = request.headers.get("content-length")
        if length is not None and int(length) > MAX_BODY:
            raise ApiError(413, "body_too_large", "Request body too large.")
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise ApiError(413, "body_too_large", "Request body too large.")
        try:
            body = ChatRequest.model_validate_json(raw)
        except ValueError as exc:
            raise ApiError(400, "invalid_body", f"Invalid request body: {str(exc)[:200]}") from None
        req = to_turn_request(body, who, tokens.users)
        model_id = settings.assistant_name.lower()
        created = int(time.time())
        completion_id = "chatcmpl-" + uuid.uuid4().hex[:24]

        try:
            prepared = await brain.prepare(req)
        except ValueError as exc:
            raise ApiError(400, "invalid_message", str(exc)) from None
        turn = prepared[0]

        if not body.stream:
            async def collect():
                last = None
                async for _, last in brain.respond(req, prepared):
                    pass
                return last

            try:
                result = await _until_first(request, asyncio.ensure_future(collect()), turn)
            except ClientGone:
                return Response(status_code=499)
            except (TurnRejected, LLMError) as exc:
                raise _status_for(exc) from None
            assert result is not None
            if result.error in {"cancelled", "preempted"} and not result.text:
                raise ApiError(409, result.error, "The turn was interrupted before it replied.")
            return JSONResponse(
                {
                    "id": completion_id, "object": "chat.completion", "created": created, "model": model_id,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": result.text},
                                 "finish_reason": "stop" if not result.error else "length"}],
                    "usage": {"completion_tokens": result.turn.stats.tokens},
                    "sc": {"turn_id": result.turn.id, "session_id": result.session.id,
                           "recalled": result.recalled, "error": result.error},
                },
                headers={"X-SC-Turn-Id": result.turn.id},
            )

        # Streaming: start the turn first so a refusal before any text is a
        # proper HTTP error, not a broken stream.
        stream = brain.respond(req, prepared)
        try:
            first = await _until_first(request, asyncio.ensure_future(stream.__anext__()), turn)
        except StopAsyncIteration:
            first = None
        except ClientGone:
            await stream.aclose()
            return Response(status_code=499)
        except (TurnRejected, LLMError) as exc:
            await stream.aclose()
            raise _status_for(exc) from None

        def chunk(delta: dict[str, str], finish: str | None) -> bytes:
            payload = {"id": completion_id, "object": "chat.completion.chunk", "created": created,
                       "model": model_id, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            return b"data: " + json.dumps(payload, ensure_ascii=False).encode() + b"\n\n"

        async def events():
            result = first[1] if first else None
            try:
                yield chunk({"role": "assistant"}, None)
                if first is not None:
                    yield chunk({"content": first[0]}, None)
                async for delta, result in stream:
                    yield chunk({"content": delta}, None)
                finish = "stop" if (result is None or not result.error) else "length"
                yield chunk({}, finish)
            except (TurnRejected, LLMError) as exc:
                err = _status_for(exc)
                yield b"data: " + json.dumps(err.body()).encode() + b"\n\n"
            finally:
                await stream.aclose()
            yield b"data: [DONE]\n\n"

        turn_id = turn.id
        return StreamingResponse(
            events(), media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-SC-Turn-Id": turn_id, "X-Accel-Buffering": "no"},
        )

    return app
