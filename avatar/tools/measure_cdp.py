"""Pull live samples from the kiosk over Chromium's loopback debug port.

Chromium must have been started with SC_AVATAR_DEBUG_PORT set (see kiosk.sh).
This does not invent frames. If the page has no __avatar object the call fails.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import time
from urllib.parse import urlparse
from urllib.request import urlopen

from sentient.services.avatar_stream.wsproto import OP_TEXT, encode_client_frame, pop_frame


class Cdp:
    def __init__(self, http_url: str) -> None:
        base = http_url.rstrip("/")
        with urlopen(base + "/json", timeout=5) as response:
            pages = json.loads(response.read().decode("utf-8"))
        page = next((item for item in pages if item.get("type") == "page" and item.get("webSocketDebuggerUrl")), None)
        if page is None:
            raise RuntimeError("no debuggable page on " + base)
        parsed = urlparse(page["webSocketDebuggerUrl"])
        self._sock = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port or 80}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise RuntimeError("cdp handshake closed")
            buf += chunk
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            raise RuntimeError("cdp handshake rejected")
        self._buf = bytearray(buf.split(b"\r\n\r\n", 1)[1])
        self._id = 0

    def close(self) -> None:
        self._sock.close()

    def evaluate(self, expression: str) -> object:
        self._id += 1
        message = {
            "id": self._id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True},
        }
        self._sock.sendall(encode_client_frame(OP_TEXT, json.dumps(message).encode("utf-8")))
        while True:
            frame = pop_frame(self._buf, client_masked=False)
            if frame is None:
                chunk = self._sock.recv(65536)
                if not chunk:
                    raise RuntimeError("cdp socket closed")
                self._buf.extend(chunk)
                continue
            opcode, payload = frame
            if opcode != OP_TEXT:
                continue
            body = json.loads(payload.decode("utf-8"))
            if body.get("id") != self._id:
                continue
            result = body.get("result", {}).get("result", {})
            if "value" in result:
                return result["value"]
            raise RuntimeError(json.dumps(body.get("result", {})))


def collect_trace(cdp_url: str, seconds: int) -> dict[str, object]:
    """Sample the renderer once a second and build the score_trace document."""
    if seconds < 1:
        raise ValueError("seconds must be positive")
    client = Cdp(cdp_url)
    fps: list[float] = []
    leads: list[float] = []
    cancels: list[float] = []
    turns = 0
    stuck = False
    reconnect = None
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            sample = client.evaluate("window.__avatar && window.__avatar.measureSample()")
            if not isinstance(sample, dict):
                raise RuntimeError("renderer did not return measureSample()")
            if isinstance(sample.get("fps"), (int, float)):
                fps.append(float(sample["fps"]))
            if isinstance(sample.get("lead_ms"), (int, float)):
                leads.append(float(sample["lead_ms"]))
            if isinstance(sample.get("cancel_ms"), (int, float)):
                cancels.append(float(sample["cancel_ms"]))
            if isinstance(sample.get("turns"), int):
                turns = sample["turns"]
            stuck = stuck or bool(sample.get("stuck"))
            if isinstance(sample.get("reconnect_s"), (int, float)):
                reconnect = float(sample["reconnect_s"])
            time.sleep(1)
    finally:
        client.close()
    return {
        "fps": fps,
        "lead_ms": leads,
        "cancel_ms": cancels,
        "turns": turns,
        "stuck": stuck,
        "reconnect_s": reconnect,
    }
