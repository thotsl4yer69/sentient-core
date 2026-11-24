"""Import the legacy Redis memory into the sc-core SQLite store.

    python -m sc.migrate --redis redis://127.0.0.1:6379/0 --owner jack --dry-run
    python -m sc.migrate --redis redis://127.0.0.1:6379/0 --owner jack

Read-only on Redis. Legacy layout (sentient/services/memory/engine.py and
services/memory.py):

* ``memory:episodic:index`` — sorted set of interaction ids by timestamp
* ``memory:episodic:<id>``  — hash; ``interaction`` is JSON with ``user_msg``,
  ``assistant_msg``, ``timestamp``
* ``memory:core``           — hash of curated facts, JSON values

Everything in the legacy store came from one user, so it is imported as that
user's private memory. Only what the person said (``user_msg``) and core facts
become memories; old assistant replies do not. Running it twice is harmless:
near-duplicates heard by the same person are refreshed, not duplicated.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass

from sc.config import load_settings
from sc.memory import MemoryStore, Session, people

logger = logging.getLogger("sc.migrate")

EPISODIC_INDEX = "memory:episodic:index"
EPISODIC = "memory:episodic"
CORE = "memory:core"


@dataclass
class Report:
    episodic_seen: int = 0
    episodic_imported: int = 0
    core_seen: int = 0
    core_imported: int = 0
    skipped: int = 0
    errors: int = 0


def _decode(v):
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else v


def _core_text(key: str, value) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    label = key.replace("_", " ").replace(".", " / ")
    return f"{label}: {value}"


def run(redis_client, store: MemoryStore, owner: str, *, dry_run: bool) -> Report:
    report = Report()
    who = people([owner])
    session = Session(id="legacy-redis-import", participants=who, started=0.0, last_active=0.0)

    for raw_id in redis_client.zrange(EPISODIC_INDEX, 0, -1):
        mid = _decode(raw_id)
        report.episodic_seen += 1
        try:
            data = redis_client.hget(f"{EPISODIC}:{mid}", "interaction")
            if data is None:
                report.skipped += 1
                continue
            item = json.loads(_decode(data))
            text = str(item.get("user_msg") or "").strip()
            ts = float(item.get("timestamp") or 0.0)
            if not text or store.worth_remembering(text) <= 0:
                report.skipped += 1
                continue
            if not dry_run:
                store.remember(session, who[0], text, now=ts or None)
            report.episodic_imported += 1
        except (ValueError, TypeError) as exc:
            report.errors += 1
            logger.warning("episodic %s unreadable: %s", mid, exc)

    for raw_key, raw_val in (redis_client.hgetall(CORE) or {}).items():
        report.core_seen += 1
        key = _decode(raw_key)
        try:
            value = json.loads(_decode(raw_val))
        except ValueError:
            value = _decode(raw_val)
        text = _core_text(key, value)
        if not dry_run:
            store.remember(session, who[0], text, importance=0.9)
        report.core_imported += 1
    return report


def main(argv: list[str] | None = None) -> int:
    import redis

    from sc.embed import MiniLMEncoder

    p = argparse.ArgumentParser(description="Import legacy Redis memory into sc-core")
    p.add_argument("--redis", default="redis://127.0.0.1:6379/0")
    p.add_argument("--owner", required=True, help="user id the legacy memories belong to, e.g. jack")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    settings = load_settings()
    client = redis.Redis.from_url(args.redis, socket_connect_timeout=3)
    client.ping()
    store = MemoryStore(settings.db_path, MiniLMEncoder(models_dir=str(settings.models_dir) if settings.models_dir else None))
    try:
        before = store.stats()["memories"]
        report = run(client, store, args.owner, dry_run=args.dry_run)
        after = store.stats()["memories"]
    finally:
        store.close()
    mode = "DRY RUN — nothing written" if args.dry_run else "imported"
    print(f"{mode}: episodic {report.episodic_imported}/{report.episodic_seen}, "
          f"core {report.core_imported}/{report.core_seen}, skipped {report.skipped}, errors {report.errors}; "
          f"store memories {before} -> {after}")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
