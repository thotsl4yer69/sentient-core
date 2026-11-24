"""A client that hangs up before the first token must free the model at once
(review finding). Real sc-core process, scripted model server."""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]


def _start_core(tmp_path, llm_url):
    jack = secrets.token_urlsafe(32)
    (tmp_path / "tokens").write_text(f"jack chat {jack}\n")
    os.chmod(tmp_path / "tokens", 0o600)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (tmp_path / "sc.toml").write_text(
        f'bind_port = {port}\nllm_url = "{llm_url}"\ndb_path = "{tmp_path / "sc.db"}"\n'
        f'tokens_file = "{tmp_path / "tokens"}"\nchat_first_token_s = 30.0\n')
    with open(tmp_path / "core.log", "w") as log:
        proc = subprocess.Popen([sys.executable, "-m", "sc.core"], cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
                                env={**os.environ, "SC_CONFIG": str(tmp_path / "sc.toml"), "PYTHONPATH": str(REPO)})
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(url + "/healthz", timeout=1)
            return proc, url, jack
        except httpx.HTTPError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError((tmp_path / "core.log").read_text())


async def _hang_up(url, token, stream: bool):
    async with httpx.AsyncClient() as c:
        try:
            await c.post(url + "/v1/chat/completions", headers={"Authorization": f"Bearer {token}"}, timeout=0.5,
                         json={"messages": [{"role": "user", "content": "tell me a long story"}], "stream": stream})
        except httpx.TimeoutException:
            pass  # hung up while Cortana was still thinking


async def _ask(url, token):
    async with httpx.AsyncClient(timeout=30) as c:
        t0 = time.monotonic()
        r = await c.post(url + "/v1/chat/completions", headers={"Authorization": f"Bearer {token}"},
                         json={"messages": [{"role": "user", "content": "are you free now?"}]})
        return r, time.monotonic() - t0


async def test_hang_up_before_first_token_frees_the_model(tmp_path, fake_llm):
    proc, url, jack = await asyncio.to_thread(_start_core, tmp_path, fake_llm.url)
    try:
        for stream in (True, False):
            # The abandoned request would take 8 s to its first token...
            fake_llm.script = type(fake_llm.script)(first_delay=8.0)
            before = fake_llm.disconnects
            await _hang_up(url, jack, stream)
            # ...the next one answers at once (a new script object; the running
            # request keeps its own).
            fake_llm.script = type(fake_llm.script)()
            r, waited = await _ask(url, jack)
            assert r.status_code == 200, r.text
            assert waited < 3.0, f"stream={stream}: waited {waited:.1f}s behind an abandoned turn"
            assert fake_llm.disconnects > before, "the abandoned request must be aborted at the model"
    finally:
        proc.terminate()
        proc.wait(10)
