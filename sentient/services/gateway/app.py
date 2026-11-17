"""HTTP application. AIRI calls the OpenAI-compatible routes. There is no HTML UI."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from sentient.common.logging import setup_logging
from sentient.services.gateway.auth import Principal, bearer_token, query_has_credential
from sentient.services.gateway.config import GatewayConfig
from sentient.services.gateway.errors import (
    GatewayError,
    authentication_required,
    insufficient_scope,
    invalid_token,
    query_credential,
)
from sentient.services.gateway.jobs import JobRunner
from sentient.services.gateway.llm import complete_via_llm, stream_via_llm
from sentient.services.gateway.mqtt_bridge import MqttBridge, now_iso
from sentient.services.gateway.policy import (
    clean_messages,
    is_forbidden_path,
    screen_messages,
    screen_tools,
)
from sentient.services.gateway.topics import SOURCE

log = setup_logging("gateway")

_NO_STORE = {"Cache-Control": "no-store"}


class ChatMessageIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: str
    content: Any = None
    name: str | None = None
    tool_calls: list[Any] | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str = "local"
    messages: list[ChatMessageIn] = Field(default_factory=list)
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    tools: list[Any] | None = None
    tool_choice: Any = None
    n: int | None = None


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    args: dict[str, Any] = Field(default_factory=dict)


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = "operator"


def create_app(config: GatewayConfig, bridge: MqttBridge | None = None) -> FastAPI:
    mqtt = bridge if bridge is not None else (MqttBridge(config.mqtt) if config.mqtt.enabled else None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if mqtt is not None:
            await mqtt.start()
        try:
            yield
        finally:
            if mqtt is not None:
                await mqtt.stop()

    app = FastAPI(
        title="sc-gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.tokens = config.tokens
    app.state.bridge = mqtt
    app.state.jobs = JobRunner(mqtt, lambda: _health_body(config, mqtt))

    @app.exception_handler(GatewayError)
    async def _on_gateway_error(_request: Request, exc: GatewayError) -> JSONResponse:
        return exc.response()

    app.add_middleware(BearerAuthMiddleware)

    @app.get("/health")
    async def health(principal: Principal = Depends(require_any)) -> JSONResponse:
        del principal
        return JSONResponse(_health_body(config, mqtt), headers=_NO_STORE)

    @app.get("/v1/models")
    async def models(principal: Principal = Depends(require("chat"))) -> JSONResponse:
        del principal
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {
                        "id": config.model,
                        "object": "model",
                        "owned_by": "sc-gateway",
                    }
                ],
            },
            headers=_NO_STORE,
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(
        body: ChatCompletionRequest,
        principal: Principal = Depends(require("chat")),
    ):
        del principal
        prepared = _prepare_chat(config, body)
        turn_id = uuid.uuid4().hex
        if body.stream:
            if mqtt is not None and not mqtt.connected:
                raise GatewayError(503, "mqtt_offline", "MQTT broker is not connected.", "server_error")
            if mqtt is None and not config.llm_url:
                raise GatewayError(503, "no_backend", "No chat backend is configured.", "server_error")
            return StreamingResponse(
                _stream_chat(config, mqtt, prepared, turn_id),
                media_type="text/event-stream",
                headers={**_NO_STORE, "X-Accel-Buffering": "no"},
            )
        text, model = await _complete_chat(config, mqtt, prepared, turn_id, stream=False)
        return JSONResponse(_completion(model, text, turn_id, prepared["messages"]), headers=_NO_STORE)

    @app.post("/v1/jobs")
    async def submit_job(
        body: JobRequest,
        principal: Principal = Depends(require("control")),
    ) -> JSONResponse:
        del principal
        record = await app.state.jobs.submit(body.type, body.args)
        return JSONResponse(record, headers=_NO_STORE)

    @app.get("/v1/jobs/{job_id}")
    async def get_job(
        job_id: str,
        principal: Principal = Depends(require("control")),
    ) -> JSONResponse:
        del principal
        return JSONResponse(app.state.jobs.get(job_id), headers=_NO_STORE)

    @app.post("/v1/turns/{turn_id}/cancel")
    async def cancel_turn(
        turn_id: str,
        body: CancelRequest | None = None,
        principal: Principal = Depends(require("control")),
    ) -> JSONResponse:
        del principal
        reason = body.reason if body is not None else "operator"
        record = await app.state.jobs.submit("turn.cancel", {"turn_id": turn_id, "reason": reason})
        return JSONResponse(record, headers=_NO_STORE)

    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
    )
    async def catch_all(full_path: str, principal: Principal = Depends(require_any)) -> JSONResponse:
        del principal
        if is_forbidden_path(full_path):
            raise GatewayError(
                403,
                "path_denied",
                "This gateway does not serve that resource.",
                "permission_error",
            )
        raise GatewayError(404, "not_found", "Not found.", "invalid_request_error")

    app.router.redirect_slashes = False
    if config.cors_origins:
        app.add_middleware(CorsAllowlist, origins=config.cors_origins)
    return app


class BearerAuthMiddleware:
    """Reject missing and invalid tokens before the body is parsed.

    This is a pure ASGI middleware so it does not buffer a streaming response.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        tokens = scope["app"].state.tokens
        try:
            principal = _authenticate_scope(scope, tokens)
        except GatewayError as exc:
            response = exc.response()
            await response(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        state["principal"] = principal
        await self.app(scope, receive, send)


class CorsAllowlist:
    """Echo an allowlisted Origin. Preflight does not require a bearer token."""

    def __init__(self, app: Any, origins: frozenset[str]) -> None:
        self.app = app
        self.origins = origins

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        origin = _header(scope, "origin")
        allowed = origin in self.origins
        if scope["method"] == "OPTIONS":
            status = 204 if allowed else 403
            headers = _cors_headers(origin) if allowed else [(b"cache-control", b"no-store")]
            await send({"type": "http.response.start", "status": status, "headers": headers})
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_cors(message: dict) -> None:
            if allowed and message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.extend(_cors_headers(origin))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_cors)


def require(scope_name: str):
    async def dependency(request: Request) -> Principal:
        principal = getattr(request.state, "principal", None)
        if not isinstance(principal, Principal):
            raise authentication_required()
        if scope_name not in principal.scopes:
            raise insufficient_scope()
        return principal

    return dependency


async def require_any(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise authentication_required()
    return principal


def _authenticate_scope(scope: dict, tokens: Any) -> Principal:
    query = parse_qs(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
    if query_has_credential(set(query)):
        raise query_credential()
    presented = bearer_token(_header(scope, "authorization"))
    if presented is None:
        raise authentication_required()
    principal = tokens.match(presented)
    if principal is None:
        log.info("auth rejected code=invalid_token path=%s", scope.get("path"))
        raise invalid_token()
    length = _header(scope, "content-length")
    if length:
        try:
            size = int(length)
        except ValueError:
            size = 0
        if size > 65536:
            raise GatewayError(413, "body_too_large", "Request body is too large.", "invalid_request_error")
    return principal


def _header(scope: dict, name: str) -> str | None:
    target = name.lower().encode("latin-1")
    for key, value in scope.get("headers") or []:
        if key.lower() == target:
            return value.decode("latin-1")
    return None


def _cors_headers(origin: str) -> list[tuple[bytes, bytes]]:
    return [
        (b"access-control-allow-origin", origin.encode("latin-1")),
        (b"access-control-allow-credentials", b"true"),
        (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
        (b"access-control-allow-headers", b"authorization, content-type"),
        (b"access-control-max-age", b"600"),
        (b"vary", b"Origin"),
    ]


def _prepare_chat(config: GatewayConfig, body: ChatCompletionRequest) -> dict[str, Any]:
    if body.n not in {None, 1}:
        raise GatewayError(400, "invalid_n", "Only one completion is accepted.", "invalid_request_error")
    if not body.messages:
        raise GatewayError(400, "empty_messages", "A user message is required.", "invalid_request_error")
    screen_messages(body.messages)
    screen_tools(body.tools)
    messages = clean_messages(body.messages)
    screen_messages(messages)
    temperature = 0.7 if body.temperature is None else body.temperature
    if temperature < 0 or temperature > 2:
        raise GatewayError(400, "temperature", "temperature must be between 0 and 2.", "invalid_request_error")
    max_tokens = 512 if body.max_tokens is None else body.max_tokens
    if max_tokens < 1 or max_tokens > 2048:
        raise GatewayError(400, "max_tokens", "max_tokens must be between 1 and 2048.", "invalid_request_error")
    model = body.model if _model_ok(body.model) else config.model
    return {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "text": next(item["content"] for item in reversed(messages) if item["role"] == "user"),
    }


def _model_ok(model: str) -> bool:
    return 1 <= len(model) <= 128 and all(char.isalnum() or char in "._:-" for char in model)


async def _complete_chat(
    config: GatewayConfig,
    bridge: MqttBridge | None,
    prepared: dict[str, Any],
    turn_id: str,
    stream: bool,
) -> tuple[str, str]:
    if bridge is not None:
        if not bridge.connected:
            raise GatewayError(503, "mqtt_offline", "MQTT broker is not connected.", "server_error")
        bridge.arm(turn_id)
        try:
            await bridge.publish_chat(_chat_payload(prepared, turn_id, stream))
            text = await bridge.wait_text(turn_id, config.chat_timeout)
        except Exception:
            bridge.discard(turn_id)
            raise
        return text, prepared["model"]
    if config.llm_url:
        text = await complete_via_llm(
            config.llm_url,
            prepared["model"],
            prepared["messages"],
            prepared["temperature"],
            prepared["max_tokens"],
            config.chat_timeout,
        )
        return text, prepared["model"]
    raise GatewayError(503, "no_backend", "No chat backend is configured.", "server_error")


async def _stream_chat(config: GatewayConfig, bridge: MqttBridge | None, prepared: dict[str, Any], turn_id: str):
    created = int(time.time())
    completion_id = f"chatcmpl-{turn_id}"
    model = prepared["model"]
    yield _sse(_chunk(completion_id, created, model, {"role": "assistant"}, None))
    try:
        if bridge is not None:
            bridge.arm(turn_id)
            try:
                await bridge.publish_chat(_chat_payload(prepared, turn_id, True))
                async for delta in bridge.iter_deltas(turn_id, config.chat_timeout):
                    yield _sse(_chunk(completion_id, created, model, {"content": delta}, None))
            except Exception:
                bridge.discard(turn_id)
                raise
        elif config.llm_url:
            async for delta in stream_via_llm(
                config.llm_url,
                model,
                prepared["messages"],
                prepared["temperature"],
                prepared["max_tokens"],
                config.chat_timeout,
            ):
                yield _sse(_chunk(completion_id, created, model, {"content": delta}, None))
        else:
            raise GatewayError(503, "no_backend", "No chat backend is configured.", "server_error")
    except GatewayError as exc:
        yield _sse({"error": {"message": exc.message, "type": exc.error_type, "code": exc.code}})
        yield b"data: [DONE]\n\n"
        return
    yield _sse(_chunk(completion_id, created, model, {}, "stop"))
    yield b"data: [DONE]\n\n"


def _chat_payload(prepared: dict[str, Any], turn_id: str, stream: bool) -> dict[str, Any]:
    return {
        "turn_id": turn_id,
        "ts": now_iso(),
        "source": SOURCE,
        "text": prepared["text"],
        "messages": prepared["messages"],
        "model": prepared["model"],
        "temperature": prepared["temperature"],
        "max_tokens": prepared["max_tokens"],
        "stream": stream,
    }


def _completion(model: str, text: str, turn_id: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    prompt = sum(max(1, len(item["content"]) // 4) for item in messages)
    completion_tokens = max(1, len(text) // 4) if text else 0
    return {
        "id": f"chatcmpl-{turn_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt + completion_tokens,
        },
    }


def _chunk(completion_id: str, created: int, model: str, delta: dict[str, str], finish: str | None) -> dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _sse(payload: dict[str, Any]) -> bytes:
    import json

    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode("utf-8")


def _health_body(config: GatewayConfig, bridge: MqttBridge | None) -> dict[str, Any]:
    return {
        "service": "sc-gateway",
        "status": "ok",
        "bind": config.host,
        "port": config.port,
        "lan": config.bind_lan,
        "tls": config.tls_cert is not None,
        "mqtt_connected": bool(bridge and bridge.connected),
        "topic_source": "design-v2",
        "topic_prefix": "sc/v1",
    }
