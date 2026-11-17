"""Refuse chat and jobs that would reach a shell, a scan, a restart, or a camera.

The gateway never executes those actions. A matching chat request is rejected
before it is published to the broker or forwarded to a loopback model, so the
text cannot reach the legacy tool runner either.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from sentient.services.gateway.errors import GatewayError

# HTTP paths that the unauthenticated web chat used to expose. They stay
# closed here even for a valid token: this process has no chat page, no
# MJPEG proxy, and no systemctl status route.
FORBIDDEN_HTTP_PATHS: tuple[str, ...] = (
    "/api/vision",
    "/vision",
    "/api/message",
    "/api/status",
    "/api/insights",
    "/insights",
    "/ws",
    "/static",
    "/v1/audio",
    "/v1/images",
    "/camera",
    "/mjpeg",
)

# Exact job types this process can carry out. There is no wildcard and no
# shell, sudo, nmap, restart, or camera job. Restarts belong to a polkit
# rule in another workstream, not to this API.
ALLOWED_JOB_TYPES: frozenset[str] = frozenset({
    "status.health",
    "status.avatar",
    "turn.cancel",
})

_SHELL = re.compile(
    r"(?i)("
    r"\bsudo\b"
    r"|\bsystemctl\b"
    r"|\bnmap\b|\bmasscan\b|\bzmap\b"
    r"|\b/bin/(?:ba)?sh\b"
    r"|\bbash\s+-c\b|\bsh\s+-c\b"
    r"|\bos\.system\s*\(|\bsubprocess\b|\bpopen\s*\("
    r"|`[^`]+`"
    r"|\$\([^)]+\)"
    r"|\b(?:run|exec|execute)\b.{0,40}\b(?:shell|bash|command)\b"
    r"|\bshell\b.{0,40}\b(?:command|exec|execute|run)\b"
    r")"
)
_SCAN = re.compile(
    r"(?i)("
    r"\bnmap\b|\bmasscan\b|\bzmap\b"
    r"|\bscan\b.{0,40}\b(?:network|subnet|lan|ports?)\b"
    r"|\b(?:network|subnet|lan)\b.{0,40}\bscan\b"
    r")"
)
_RESTART = re.compile(
    r"(?i)("
    r"\bsystemctl\b"
    r"|\bsudo\b"
    r"|\brestart\b.{0,60}\b(?:service|systemctl|sentient-[a-z0-9-]+|sc-[a-z0-9-]+)\b"
    r"|\b(?:stop|kill|disable|mask)\b.{0,40}\b(?:service|systemctl)\b"
    r")"
)
_CAMERA = re.compile(
    r"(?i)("
    r"/api/vision"
    r"|vision/stream"
    r"|\bmjpeg\b"
    r"|\bcamera\s+streams?\b"
    r"|multipart/x-mixed-replace"
    r")"
)

_CATEGORY_MESSAGE = {
    "shell": "This gateway does not run shell commands.",
    "scan": "This gateway does not scan the network.",
    "restart": "This gateway does not restart services.",
    "camera": "This gateway does not proxy camera streams.",
    "tool": "This gateway does not execute tools.",
    "media": "This gateway accepts text only.",
}


def is_forbidden_path(path: str) -> bool:
    normalized = "/" + path.lstrip("/")
    if normalized != "/" and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    for prefix in FORBIDDEN_HTTP_PATHS:
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return True
    return False


def classify_text(text: str) -> str | None:
    """Return the denial category for a piece of chat text, or None."""
    if not text:
        return None
    if _CAMERA.search(text):
        return "camera"
    if _SCAN.search(text):
        return "scan"
    if _RESTART.search(text):
        return "restart"
    if _SHELL.search(text):
        return "shell"
    return None


def refusal(category: str) -> GatewayError:
    message = _CATEGORY_MESSAGE.get(category, "This gateway refused the request.")
    return GatewayError(403, f"chat_denied_{category}", message, "permission_error")


def extract_text(content: Any) -> str:
    """Return the text of a chat message. Non-text parts are a media refusal."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
                continue
            if not isinstance(part, dict) or part.get("type") != "text":
                raise refusal("media")
            text = part.get("text")
            if text is None:
                continue
            if not isinstance(text, str):
                raise refusal("media")
            pieces.append(text)
        return "\n".join(pieces)
    raise refusal("media")


def screen_messages(messages: Iterable[Any]) -> None:
    """Screen user, system, and tool text. Assistant history is not re-screened."""
    for message in messages:
        role = getattr(message, "role", None)
        if role is None and isinstance(message, dict):
            role = message.get("role")
        if role not in {"user", "system", "tool"}:
            continue
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        category = classify_text(extract_text(content))
        if category:
            raise refusal(category)


def screen_tools(tools: list[Any] | None) -> None:
    if not tools:
        return
    for tool in tools:
        blob = json.dumps(tool, default=str)
        category = classify_text(blob)
        if category:
            raise refusal(category if category != "shell" else "tool")
        name = ""
        if isinstance(tool, dict):
            function = tool.get("function")
            if isinstance(function, dict) and isinstance(function.get("name"), str):
                name = function["name"]
            elif isinstance(tool.get("name"), str):
                name = tool["name"]
        if name and classify_text(name.replace("_", " ")):
            raise refusal("tool")


def clean_messages(messages: Iterable[Any]) -> list[dict[str, str]]:
    """Text-only transcript. Tool calls are dropped and never forwarded."""
    cleaned: list[dict[str, str]] = []
    for message in messages:
        role = getattr(message, "role", None)
        if role is None and isinstance(message, dict):
            role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise GatewayError(400, "invalid_role", "Unknown message role.", "invalid_request_error")
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        text = extract_text(content)
        if not text:
            continue
        if len(text) > 16000:
            raise GatewayError(400, "message_too_large", "A message is too large.", "invalid_request_error")
        cleaned.append({"role": role, "content": text})
    if not cleaned or not any(item["role"] == "user" for item in cleaned):
        raise GatewayError(400, "empty_messages", "A user message is required.", "invalid_request_error")
    if len(cleaned) > 40:
        raise GatewayError(400, "too_many_messages", "Too many messages.", "invalid_request_error")
    return cleaned


def job_type_denied(job_type: str) -> GatewayError:
    shown = job_type if len(job_type) <= 64 and job_type.isprintable() else "rejected"
    allowlist = ", ".join(sorted(ALLOWED_JOB_TYPES))
    return GatewayError(
        403,
        "job_type_denied",
        f"Job type {shown!r} is not allowlisted. Allowlist: {allowlist}.",
        "permission_error",
    )
