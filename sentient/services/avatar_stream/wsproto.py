"""Minimal WebSocket frames for the avatar stream. Server frames are unmasked."""

from __future__ import annotations

import base64
import hashlib

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 8 * 1024 * 1024

OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


def accept_key(sec_websocket_key: str) -> str:
    digest = hashlib.sha1((sec_websocket_key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def handshake_response(sec_websocket_key: str) -> bytes:
    accept = accept_key(sec_websocket_key)
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    ).encode("ascii")


def encode_frame(opcode: int, payload: bytes = b"") -> bytes:
    length = len(payload)
    head = bytearray([0x80 | (opcode & 0x0F)])
    if length < 126:
        head.append(length)
    elif length < 65536:
        head.append(126)
        head.extend(length.to_bytes(2, "big"))
    else:
        head.append(127)
        head.extend(length.to_bytes(8, "big"))
    head.extend(payload)
    return bytes(head)


def encode_client_frame(opcode: int, payload: bytes = b"", mask: bytes = b"\x01\x02\x03\x04") -> bytes:
    """Masked frame, as a browser client sends it."""
    if len(mask) != 4:
        raise ValueError("mask must be 4 bytes")
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    length = len(payload)
    head = bytearray([0x80 | (opcode & 0x0F)])
    if length < 126:
        head.append(0x80 | length)
    elif length < 65536:
        head.append(0x80 | 126)
        head.extend(length.to_bytes(2, "big"))
    else:
        head.append(0x80 | 127)
        head.extend(length.to_bytes(8, "big"))
    head.extend(mask)
    head.extend(masked)
    return bytes(head)


def encode_close(code: int = 1000, reason: str = "") -> bytes:
    body = code.to_bytes(2, "big") + reason.encode("utf-8")[:123]
    return encode_frame(OP_CLOSE, body)


def pop_frame(buf: bytearray, *, client_masked: bool) -> tuple[int, bytes] | None:
    """Pop one complete frame from ``buf``. Returns None if more bytes are needed."""
    if len(buf) < 2:
        return None
    opcode = buf[0] & 0x0F
    masked = bool(buf[1] & 0x80)
    length = buf[1] & 0x7F
    offset = 2
    if length == 126:
        if len(buf) < 4:
            return None
        length = int.from_bytes(buf[2:4], "big")
        offset = 4
    elif length == 127:
        if len(buf) < 10:
            return None
        length = int.from_bytes(buf[2:10], "big")
        offset = 10
    if length > MAX_FRAME:
        raise ValueError("frame exceeds 8 MiB")
    if masked != client_masked:
        raise ValueError("unexpected mask bit")
    mask_len = 4 if masked else 0
    total = offset + mask_len + length
    if len(buf) < total:
        return None
    payload = bytearray(buf[offset + mask_len : total])
    if masked:
        mask = buf[offset : offset + 4]
        for index, value in enumerate(payload):
            payload[index] = value ^ mask[index % 4]
    del buf[:total]
    return opcode, bytes(payload)
