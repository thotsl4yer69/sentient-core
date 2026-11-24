"""Legacy Redis -> SQLite import, against a real redis-server."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time

import pytest

from sc.memory import MemoryStore
from sc.migrate import run


@pytest.fixture
def redis_client(tmp_path):
    redis = pytest.importorskip("redis")
    if shutil.which("redis-server") is None:
        pytest.skip("redis-server not installed")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(["redis-server", "--port", str(port), "--save", "", "--dir", str(tmp_path)],
                            stdout=subprocess.DEVNULL)
    client = redis.Redis(port=port)
    for _ in range(50):
        try:
            client.ping()
            break
        except redis.ConnectionError:
            time.sleep(0.1)
    yield client
    proc.terminate()
    proc.wait(5)


def legacy_episode(client, mid, user_msg, assistant_msg, ts):
    """Exactly how sentient/services/memory/engine.py _store_episodic writes."""
    interaction = {"user_msg": user_msg, "assistant_msg": assistant_msg, "timestamp": ts,
                   "importance_score": 0.5, "interaction_id": mid}
    client.hset(f"memory:episodic:{mid}", mapping={
        "interaction": json.dumps(interaction), "embedding": json.dumps([0.0] * 384),
        "tags": json.dumps([]), "stored_at": ts})
    client.zadd("memory:episodic:index", {mid: ts})


def test_import_is_private_idempotent_and_recallable(redis_client, tmp_path, encoder):
    legacy_episode(redis_client, "a1", "My workshop is in the back shed", "Got it.", 1_700_000_000.0)
    legacy_episode(redis_client, "a2", "hi", "Hey!", 1_700_000_100.0)  # filler: skipped
    legacy_episode(redis_client, "a3", "The Jaguar's fuel pump fuse is F81, twenty amps", "Noted.", 1_700_000_200.0)
    redis_client.zadd("memory:episodic:index", {"ghost": 1_700_000_300.0})  # index without a hash
    redis_client.hset("memory:core", mapping={"name": json.dumps("Jack"),
                                              "preferences.coffee": json.dumps("black, one sugar")})
    store = MemoryStore(tmp_path / "sc.db", encoder)
    try:
        dry = run(redis_client, store, "jack", dry_run=True)
        assert store.stats()["memories"] == 0
        assert (dry.episodic_imported, dry.core_imported, dry.skipped) == (2, 2, 2)

        first = run(redis_client, store, "jack", dry_run=False)
        n = store.stats()["memories"]
        assert n == 4 and first.errors == 0
        run(redis_client, store, "jack", dry_run=False)
        assert store.stats()["memories"] == n, "second import must not duplicate"

        jack = store.open_session(["jack"], idle_s=60)
        partner = store.open_session(["partner"], idle_s=60)
        got = store.recall(jack, "which fuse is the fuel pump", k=3, min_score=0.3)
        assert any("F81" in m.text for m in got)
        assert any(abs(m.ts - 1_700_000_200.0) < 1 for m in got), "original timestamps are kept"
        assert not store.recall(partner, "which fuse is the fuel pump", k=3, min_score=0.0)
        assert any("black, one sugar" in m.text for m in store.recall(jack, "how do I take coffee", k=3, min_score=0.2))
        assert redis_client.zcard("memory:episodic:index") == 4, "Redis is read-only for the import"
    finally:
        store.close()
