"""sc-core as real processes: real llama-server, real MiniLM, real HTTP.

Here (sandbox) this proves wiring and behaviour with a small CPU model. On nanob
the same file, pointed at Gemma 4 E2B with SC_TEST_LLM_URL, is the B1/D1 check:

    SC_TEST_LLM_URL=http://127.0.0.1:18080 SC_TEST_STRICT=1 python -m pytest tests/sc/test_real_model.py -s

SC_TEST_STRICT=1 also enforces the TARGET.md latency and recall numbers, which a
0.5B CPU model in a sandbox isn't expected to meet.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[2]
STRICT = os.environ.get("SC_TEST_STRICT") == "1"


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def core(tmp_path):
    """Start ``python -m sc.core`` as a subprocess; yields (base_url, tokens, restart)."""
    procs = []
    jack, partner, voice = (secrets.token_urlsafe(32) for _ in range(3))
    (tmp_path / "tokens").write_text(f"jack chat {jack}\npartner chat {partner}\nvoice chat+speaker {voice}\n")
    os.chmod(tmp_path / "tokens", 0o600)
    port = _port()

    def start(llm_url: str):
        cfg = tmp_path / "sc.toml"
        cfg.write_text(
            f'bind_port = {port}\nllm_url = "{llm_url}"\ndb_path = "{tmp_path / "sc.db"}"\n'
            f'tokens_file = "{tmp_path / "tokens"}"\n'
            # Deterministic sampling so a pass/fail reflects the pipeline, not chance.
            "temperature = 0.0\n"
        )
        env = {**os.environ, "SC_CONFIG": str(cfg), "PYTHONPATH": str(REPO)}
        with open(tmp_path / f"core{len(procs)}.log", "w") as log:
            p = subprocess.Popen([sys.executable, "-m", "sc.core", "--voice"], cwd=REPO, env=env,
                                 stdout=log, stderr=subprocess.STDOUT)
        procs.append(p)
        url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                if httpx.get(url + "/healthz", timeout=1).status_code in (200, 503):
                    return url
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError((tmp_path / f"core{len(procs) - 1}.log").read_text())

    def stop():
        p = procs[-1]
        p.terminate()
        p.wait(10)

    yield start, stop, {"jack": jack, "partner": partner, "voice": voice}
    for p in procs:
        if p.poll() is None:
            p.kill()


def ask(url, token, text, **extra):
    r = httpx.post(url + "/v1/chat/completions", headers={"Authorization": f"Bearer {token}"},
                   json={"messages": [{"role": "user", "content": text}], **extra}, timeout=180)
    r.raise_for_status()
    return r.json()


def test_ten_text_turns_with_context_and_timing(real_llm_url, core):
    """B1 shape: 10 turns, each a real model reply; turn 2 sees turn 1."""
    start, _, tok = core
    url = start(real_llm_url)
    prompts = [
        "Hi Cortana, I'm testing you. My favourite colour is teal.",
        "What did I just say my favourite colour was? One word.",
        "Give me one tip for keeping a Jetson cool.",
        "Say something short and witty about Mondays.",
        "What's two plus two? Just the number.",
        "Name a planet.",
        "In one sentence, what is a kelpie?",
        "Tell me a very short fact about Melbourne.",
        "Reply with the word ready.",
        "Thanks, that's all for now.",
    ]
    first_tokens, totals, replies = [], [], []
    for p in prompts:
        t0 = time.monotonic()
        with httpx.stream("POST", url + "/v1/chat/completions",
                          headers={"Authorization": f"Bearer {tok['jack']}"},
                          json={"messages": [{"role": "user", "content": p}], "stream": True},
                          timeout=180) as r:
            assert r.status_code == 200
            text, first = "", None
            for line in r.iter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                d = json.loads(line[6:])
                assert "error" not in d, d
                piece = d["choices"][0]["delta"].get("content")
                if piece:
                    first = first or time.monotonic() - t0
                    text += piece
        totals.append(time.monotonic() - t0)
        first_tokens.append(first)
        replies.append(text)
    assert all(r.strip() for r in replies), replies
    assert "teal" in replies[1].lower(), f"context not used: {replies[1]!r}"
    print(f"\nfirst token p50={statistics.median(first_tokens):.2f}s max={max(first_tokens):.2f}s; "
          f"total p50={statistics.median(totals):.2f}s max={max(totals):.2f}s")
    if STRICT:
        assert sorted(first_tokens)[8] <= 2.0 and sorted(totals)[8] <= 10.0


def test_memory_survives_restart_and_stays_private(real_llm_url, core):
    start, stop, tok = core
    url = start(real_llm_url)
    ask(url, tok["jack"], "Remember this: my dog is called Biscuit and she's a kelpie.")
    ask(url, tok["jack"], "Remember this: the spare house key is under the blue pot.")
    stop()
    url = start(real_llm_url)  # new process, same database
    reply = ask(url, tok["jack"], "What's my dog called? Answer with just the name.")
    assert reply["sc"]["recalled"] >= 1
    assert "biscuit" in reply["choices"][0]["message"]["content"].lower()
    other = ask(url, tok["partner"], "Where is the spare house key hidden?")
    assert other["sc"]["recalled"] == 0, "partner must not see Jack's private memories"


def test_client_disconnect_stops_generation(real_llm_url, core):
    start, _, tok = core
    url = start(real_llm_url)
    with httpx.stream("POST", url + "/v1/chat/completions",
                      headers={"Authorization": f"Bearer {tok['jack']}"},
                      json={"messages": [{"role": "user", "content": "Count from one to two hundred in words."}],
                            "stream": True}, timeout=60) as r:
        for i, line in enumerate(r.iter_lines()):
            if i > 6:
                break
    # The slot must be free again quickly: a new turn starts promptly.
    t0 = time.monotonic()
    ask(url, tok["jack"], "Reply with the word ok.")
    assert time.monotonic() - t0 < 30
    health = httpx.get(url + "/healthz", timeout=5).json()
    assert health["active_turn"] is None and health["queued"] == 0


def test_voice_turn_is_short(real_llm_url, core):
    start, _, tok = core
    url = start(real_llm_url)
    r = ask(url, tok["voice"], "Tell me about the history of the Roman empire.",
            sc_kind="voice", sc_speaker="jack")
    text = r["choices"][0]["message"]["content"]
    assert text.strip()
    assert r["usage"]["completion_tokens"] <= 160
