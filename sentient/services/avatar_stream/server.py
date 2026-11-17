"""Authenticated WebSocket server that feeds the display node one turn at a time."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from dataclasses import dataclass

from sentient.services.avatar_stream.auth import token_matches
from sentient.services.avatar_stream.hub import TurnHub
from sentient.services.avatar_stream.package import TurnPackage, package_turn
from sentient.services.avatar_stream.wsproto import (
    OP_BINARY,
    OP_PING,
    OP_PONG,
    OP_TEXT,
    encode_close,
    encode_frame,
    handshake_response,
    pop_frame,
)

LOG = logging.getLogger("sentient.avatar_stream")
AUTH_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class StreamConfig:
    bind: str
    port: int
    display_token: str
    ingest_token: str
    tls_cert: str = ""
    tls_key: str = ""


class AvatarStreamServer:
    def __init__(self, config: StreamConfig, hub: TurnHub | None = None) -> None:
        self.config = config
        self.hub = hub or TurnHub()
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        ssl_ctx = None
        if self.config.tls_cert and self.config.tls_key:
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.load_cert_chain(self.config.tls_cert, self.config.tls_key)
        self._server = await asyncio.start_server(
            self._handle,
            self.config.bind,
            self.config.port,
            ssl=ssl_ctx,
        )

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            return self.config.port
        return int(self._server.sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await self._session(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError, EOFError, ValueError) as exc:
            LOG.debug("stream connection closed: %s", exc)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = await reader.read(4096)
            if not chunk:
                return
            header += chunk
            if len(header) > 16384:
                return
        raw, leftover = header.split(b"\r\n\r\n", 1)
        lines = raw.decode("latin1").split("\r\n")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        ws_key = headers.get("sec-websocket-key", "")
        if not ws_key or headers.get("upgrade", "").lower() != "websocket":
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return
        writer.write(handshake_response(ws_key))
        await writer.drain()
        buf = bytearray(leftover)
        role = await self._auth(reader, writer, buf)
        if role == "display":
            await self._display(reader, writer, buf)
        elif role == "ingest":
            await self._ingest(reader, writer, buf)

    async def _auth(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, buf: bytearray) -> str:
        try:
            opcode, payload = await asyncio.wait_for(
                self._read_data(reader, writer, buf),
                AUTH_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            writer.write(encode_close(4401, "auth timeout"))
            await writer.drain()
            return ""
        if opcode != OP_TEXT:
            writer.write(encode_close(4401, "auth required"))
            await writer.drain()
            return ""
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            message = None
        token = message.get("token") if isinstance(message, dict) else None
        role = message.get("role") if isinstance(message, dict) else None
        if role == "display":
            expected = self.config.display_token
        elif role == "ingest":
            expected = self.config.ingest_token
        else:
            expected = ""
        if not token_matches(token, expected):
            writer.write(encode_close(4401, "unauthorized"))
            await writer.drain()
            return ""
        return str(role)

    async def _read_data(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        buf: bytearray,
    ) -> tuple[int, bytes]:
        while True:
            frame = pop_frame(buf, client_masked=True)
            if frame is None:
                chunk = await reader.read(65536)
                if not chunk:
                    raise EOFError
                buf.extend(chunk)
                continue
            opcode, payload = frame
            if opcode == OP_PING:
                writer.write(encode_frame(OP_PONG, payload))
                await writer.drain()
                continue
            if opcode in (OP_TEXT, OP_BINARY):
                return opcode, payload
            if opcode == 0x8:
                raise EOFError

    async def _display(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        buf: bytearray,
    ) -> None:
        loop = asyncio.get_running_loop()
        outgoing: asyncio.Queue[dict[str, object]] = asyncio.Queue()

        def push(message: dict[str, object]) -> None:
            loop.call_soon_threadsafe(outgoing.put_nowait, message)

        self.hub.subscribe(push)
        ready = json.dumps({"type": "auth_ok", "role": "display"}).encode("utf-8")
        writer.write(encode_frame(OP_TEXT, ready))
        await writer.drain()
        incoming: asyncio.Queue[tuple[int, bytes] | None] = asyncio.Queue()

        async def pump_socket() -> None:
            try:
                while True:
                    await incoming.put(await self._read_data(reader, writer, buf))
            except asyncio.CancelledError:
                raise
            except (EOFError, ConnectionError, OSError, ValueError):
                await incoming.put(None)

        pump = asyncio.create_task(pump_socket())
        try:
            while True:
                socket_task = asyncio.create_task(incoming.get())
                hub_task = asyncio.create_task(outgoing.get())
                done, pending = await asyncio.wait(
                    {socket_task, hub_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                for task in pending:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                if socket_task in done:
                    item = socket_task.result()
                    if item is None:
                        return
                    opcode, payload = item
                    if opcode == OP_TEXT:
                        self._display_command(payload)
                if hub_task in done:
                    await self._send_hub(writer, hub_task.result())
        finally:
            self.hub.unsubscribe(push)
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass

    def _display_command(self, payload: bytes) -> None:
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(message, dict) and message.get("type") == "cancel":
            turn_id = message.get("turn_id")
            if isinstance(turn_id, str) and turn_id:
                self.hub.cancel(turn_id, str(message.get("reason") or "client"))

    async def _send_hub(self, writer: asyncio.StreamWriter, message: dict[str, object]) -> None:
        kind = message.get("type")
        if kind == "cancel":
            body = json.dumps(
                {
                    "type": "cancel",
                    "turn_id": message["turn_id"],
                    "reason": message.get("reason", "barge-in"),
                }
            ).encode("utf-8")
            writer.write(encode_frame(OP_TEXT, body))
            await writer.drain()
            return
        if kind != "turn":
            return
        package = message["package"]
        if not isinstance(package, TurnPackage):
            return
        writer.write(encode_frame(OP_TEXT, json.dumps(package.header()).encode("utf-8")))
        writer.write(encode_frame(OP_BINARY, package.pcm_s16le))
        await writer.drain()

    async def _ingest(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        buf: bytearray,
    ) -> None:
        pending: dict[str, object] | None = None
        ready = json.dumps({"type": "auth_ok", "role": "ingest"}).encode("utf-8")
        writer.write(encode_frame(OP_TEXT, ready))
        await writer.drain()
        while True:
            opcode, payload = await self._read_data(reader, writer, buf)
            if opcode == OP_TEXT:
                try:
                    message = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    writer.write(encode_close(4400, "bad json"))
                    await writer.drain()
                    return
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "cancel":
                    turn_id = message.get("turn_id")
                    if isinstance(turn_id, str) and turn_id:
                        self.hub.cancel(turn_id, str(message.get("reason") or "ingest"))
                    continue
                if message.get("type") == "turn":
                    pending = message
                continue
            if opcode == OP_BINARY and pending is not None:
                self._accept_turn(pending, payload)
                pending = None

    def _accept_turn(self, header: dict[str, object], pcm: bytes) -> None:
        turn_id = header.get("turn_id")
        sample_rate = header.get("sample_rate")
        alignments = header.get("alignments") or []
        if not isinstance(turn_id, str) or not isinstance(sample_rate, int):
            return
        if not isinstance(alignments, list):
            return
        if self.hub.is_cancelled(turn_id):
            return
        try:
            package = package_turn(turn_id, pcm, sample_rate, alignments)
        except ValueError as exc:
            LOG.info("rejected turn %s: %s", turn_id, exc)
            return
        self.hub.submit(package)
