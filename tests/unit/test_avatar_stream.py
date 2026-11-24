"""Phoneme timeline, stream auth and cancel handling."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

from sentient.services.avatar_stream.auth import token_matches
from sentient.services.avatar_stream.hub import CANCEL_DEADLINE_MS, TurnHub
from sentient.services.avatar_stream.package import package_turn
from sentient.services.avatar_stream.server import AvatarStreamServer, StreamConfig
from sentient.services.avatar_stream.timeline import alignments_to_cues
from sentient.services.avatar_stream.visemes import OCULUS_VISEMES, map_phoneme
from sentient.services.avatar_stream.wsproto import OP_BINARY, OP_CLOSE, OP_TEXT, encode_client_frame, pop_frame

ROOT = Path(__file__).resolve().parents[2]


def test_oculus_set_has_fifteen_names():
    assert len(OCULUS_VISEMES) == 15
    assert OCULUS_VISEMES[0] == "sil"
    assert "PP" in OCULUS_VISEMES and "aa" in OCULUS_VISEMES


def test_piper_phonemes_map_to_oculus_visemes():
    cases = {
        "p": "PP",
        "b": "PP",
        "m": "PP",
        "f": "FF",
        "v": "FF",
        "θ": "TH",
        "ð": "TH",
        "t": "DD",
        "d": "DD",
        "k": "kk",
        "ɡ": "kk",
        "tʃ": "CH",
        "ʃ": "CH",
        "s": "SS",
        "z": "SS",
        "n": "nn",
        "ŋ": "nn",
        "l": "nn",
        "ɹ": "RR",
        "ɑ": "aa",
        "æ": "aa",
        "ə": "aa",
        "ɛ": "E",
        "eɪ": "E",
        "iː": "I",
        "ɪ": "I",
        "oʊ": "O",
        "ɔ": "O",
        "u": "U",
        "ʊ": "U",
        "^": "sil",
        "$": "sil",
        "_": "sil",
    }
    for phoneme, viseme in cases.items():
        mapped, known = map_phoneme(phoneme)
        assert known, phoneme
        assert mapped == viseme, phoneme
    assert map_phoneme("not-a-phoneme") == ("sil", False)


def test_alignment_timings_follow_the_sample_clock():
    cues = alignments_to_cues(
        [
            {"phoneme": "^", "num_samples": 2205},
            {"phoneme": "p", "num_samples": 4410},
            {"phoneme": "p", "num_samples": 2205},
            {"phoneme": "ɑ", "num_samples": 6615},
            {"phoneme": "$", "num_samples": 2205},
        ],
        22050,
    )
    assert [(cue.viseme, cue.start_ms, cue.duration_ms) for cue in cues] == [
        ("sil", 0, 100),
        ("PP", 100, 300),
        ("aa", 400, 300),
        ("sil", 700, 100),
    ]


def test_package_turn_rejects_bad_pcm_and_keeps_turn_id():
    package = package_turn(
        "turn-7",
        b"\x00\x00" * 220,
        22050,
        [{"phoneme": "m", "num_samples": 220}],
    )
    assert package.turn_id == "turn-7"
    assert package.visemes == ("PP",)
    assert package.header()["pcm_bytes"] == 440
    try:
        package_turn("turn-7", b"\x00", 22050, [])
    except ValueError:
        pass
    else:
        raise AssertionError("odd pcm must be rejected")


def test_tokens_fail_closed():
    assert token_matches("display-token", "display-token")
    assert not token_matches("display-token", "other")
    assert not token_matches("", "display-token")
    assert not token_matches("display-token", "")
    assert not token_matches(None, "display-token")


def test_cancel_drops_the_turn_within_the_deadline():
    hub = TurnHub()
    seen = []
    hub.subscribe(seen.append)
    package = package_turn("t-live", b"\x00\x00" * 10, 22050, [{"phoneme": "ɑ", "num_samples": 10}])
    assert hub.submit(package)
    elapsed = hub.cancel("t-live", "barge-in")
    assert elapsed < CANCEL_DEADLINE_MS
    assert hub.active("t-live") is None
    assert not hub.submit(package)
    kinds = [item["type"] for item in seen]
    assert kinds == ["turn", "cancel"]


def test_unit_file_matches_ws8_conventions():
    text = (ROOT / "sentient/services/avatar_stream/sc-avatar-stream.service").read_text(encoding="utf-8")
    assert "PartOf=sc.target" in text
    assert "WantedBy=sc.target" in text
    assert "User=sc" in text
    assert "Group=sc" in text
    assert "/opt/sc/current/venv/bin/python -m sentient.services.avatar_stream" in text


def _request(key: str) -> bytes:
    return (
        "GET /avatar-stream HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    ).encode("ascii")


async def _connect(port: int):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    key = base64.b64encode(b"0123456789abcdef").decode("ascii")
    writer.write(_request(key))
    await writer.drain()
    data = b""
    while b"\r\n\r\n" not in data:
        data += await reader.read(4096)
    status = data.split(b"\r\n", 1)[0]
    assert b" 101 " in status
    return reader, writer, bytearray(data.split(b"\r\n\r\n", 1)[1])


async def _send(writer, opcode: int, payload: bytes) -> None:
    writer.write(encode_client_frame(opcode, payload))
    await writer.drain()


async def _recv(reader, buf: bytearray):
    while True:
        frame = pop_frame(buf, client_masked=False)
        if frame is not None:
            return frame
        chunk = await reader.read(65536)
        if not chunk:
            raise EOFError
        buf.extend(chunk)


async def _auth(port: int, role: str, token: str):
    reader, writer, buf = await _connect(port)
    await _send(writer, OP_TEXT, json.dumps({"type": "auth", "role": role, "token": token}).encode("utf-8"))
    opcode, payload = await _recv(reader, buf)
    return reader, writer, buf, opcode, payload


def test_stream_auth_turn_and_cancel():
    asyncio.run(_stream_session())


async def _stream_session() -> None:
    server = AvatarStreamServer(
        StreamConfig("127.0.0.1", 0, "display-token", "ingest-token"),
    )
    await server.start()
    port = server.port()
    try:
        _reader, writer, _buf, opcode, payload = await _auth(port, "display", "nope")
        assert opcode == OP_CLOSE
        assert int.from_bytes(payload[:2], "big") == 4401
        writer.close()
        await writer.wait_closed()

        display_r, display_w, display_buf, opcode, payload = await _auth(port, "display", "display-token")
        assert opcode == OP_TEXT
        assert json.loads(payload)["role"] == "display"
        ingest_r, ingest_w, ingest_buf, opcode, payload = await _auth(port, "ingest", "ingest-token")
        assert json.loads(payload)["role"] == "ingest"

        header = {
            "type": "turn",
            "turn_id": "t-stream",
            "sample_rate": 22050,
            "alignments": [
                {"phoneme": "p", "num_samples": 2205},
                {"phoneme": "ɑ", "num_samples": 4410},
            ],
        }
        pcm = b"\x10\x00" * 6615
        await _send(ingest_w, OP_TEXT, json.dumps(header).encode("utf-8"))
        await _send(ingest_w, OP_BINARY, pcm)
        opcode, payload = await asyncio.wait_for(_recv(display_r, display_buf), 2)
        assert opcode == OP_TEXT
        message = json.loads(payload)
        assert message["turn_id"] == "t-stream"
        assert message["visemes"] == ["PP", "aa"]
        assert message["vtimes"] == [0, 100]
        assert message["vdurations"] == [100, 200]
        opcode, payload = await asyncio.wait_for(_recv(display_r, display_buf), 2)
        assert opcode == OP_BINARY
        assert payload == pcm

        elapsed = server.hub.cancel("t-stream", "barge-in")
        assert elapsed < CANCEL_DEADLINE_MS
        opcode, payload = await asyncio.wait_for(_recv(display_r, display_buf), 2)
        assert opcode == OP_TEXT
        assert json.loads(payload)["type"] == "cancel"

        ingest_w.close()
        display_w.close()
        await ingest_w.wait_closed()
        await display_w.wait_closed()
        ingest_r.feed_eof()
    finally:
        await server.close()
