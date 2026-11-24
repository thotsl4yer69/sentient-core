"""Durable conversation log and long-term memory in one SQLite file.

Why SQLite and not Redis: one file survives power loss (WAL, synchronous=FULL),
backs up with the online-backup API, and needs no extra daemon or RAM.

Privacy rule (one rule, applied everywhere): **a memory is only ever shown in a
session whose people were all present when it was said.** A memory carries the
set of people who heard it (``participants``). It is visible to a session
``P`` iff ``P ⊆ participants``. So:

* something Jack said alone is visible only when Jack is alone;
* something said with Jack and his partner present is visible to either of them
  alone or together, but not to Jack with a third person.

What gets remembered: what people say (user turns), with embeddings for recall.
The assistant's own replies are logged but never become memories, so a model
mistake can't turn into a remembered "fact".
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

logger = logging.getLogger("sc.memory")

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    participants TEXT NOT NULL,         -- JSON sorted list of user ids
    started REAL NOT NULL,
    last_active REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_by_people ON sessions(participants, last_active);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    turn_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    speaker TEXT NOT NULL,
    content TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    speaker TEXT NOT NULL,
    participants TEXT NOT NULL,         -- JSON sorted list: who heard it
    session_id TEXT,
    text TEXT NOT NULL,
    ts REAL NOT NULL,
    last_recalled REAL,
    importance REAL NOT NULL DEFAULT 0.5,
    embedding BLOB NOT NULL             -- float32[384], L2-normalised
);
CREATE INDEX IF NOT EXISTS memories_by_speaker ON memories(speaker);
"""

_FILLER = re.compile(
    r"^\s*(hi|hey|hello|yo|ok(ay)?|thanks?( you)?|cheers|yes|yeah|yep|no|nope|"
    r"good (morning|night|evening)|bye|see ya|lol|haha)[\s.!?,]*$",
    re.IGNORECASE,
)
_EXPLICIT = re.compile(r"\b(remember|don'?t forget|note that|keep in mind)\b", re.IGNORECASE)
# Questions aren't facts: storing "what's my dog's name?" would outrank the
# answer the next time it's asked.
_QUESTION = re.compile(
    r"^\s*(what|what's|whats|who|who's|where|where's|when|why|how|which|whose|is|are|am|do|does|did|"
    r"can|could|would|will|should|have|has|tell me|remind me)\b",
    re.IGNORECASE,
)


class Encoder(Protocol):
    def encode(self, text: str) -> np.ndarray: ...


@dataclass(frozen=True)
class Session:
    id: str
    participants: tuple[str, ...]
    started: float
    last_active: float
    is_new: bool = False

    @property
    def label(self) -> str:
        return " + ".join(self.participants)


@dataclass(frozen=True)
class Message:
    role: str
    speaker: str
    content: str
    ts: float


@dataclass(frozen=True)
class Memory:
    id: int
    speaker: str
    participants: tuple[str, ...]
    text: str
    ts: float
    score: float


def people(users: Iterable[str]) -> tuple[str, ...]:
    out = tuple(sorted({u.strip().lower() for u in users if u and u.strip()}))
    if not out:
        raise ValueError("a session needs at least one person")
    return out


class MemoryStore:
    """Thread-safe; every public method takes the lock and runs one transaction."""

    def __init__(self, path: Path, encoder: Encoder, *, dedupe_threshold: float = 0.95):
        self.path = Path(path)
        self.encoder = encoder
        self.dedupe_threshold = dedupe_threshold
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA busy_timeout=5000")
        # executescript() issues its own COMMIT, so it can't sit inside _tx().
        # Every statement is CREATE ... IF NOT EXISTS, so a crash midway is safe.
        with self._lock:
            self._db.executescript(_SCHEMA)
        with self._tx():
            row = self._db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                self._db.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            elif int(row[0]) > SCHEMA_VERSION:
                raise RuntimeError(f"{self.path} schema {row[0]} is newer than this build ({SCHEMA_VERSION})")
        # In-RAM index for recall: ids, participant sets, float16 embeddings.
        self._ids: list[int] = []
        self._who: list[frozenset[str]] = []
        self._vecs = np.zeros((0, 384), dtype=np.float16)
        self._load_index()

    # ── plumbing ──────────────────────────────────────────────────────────
    def _tx(self):
        store = self

        class _Tx:
            def __enter__(self_inner):
                store._lock.acquire()
                store._db.execute("BEGIN IMMEDIATE")
                return store._db

            def __exit__(self_inner, exc_type, exc, tb):
                try:
                    store._db.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    store._lock.release()
                return False

        return _Tx()

    def _load_index(self) -> None:
        with self._lock:
            rows = self._db.execute("SELECT id, participants, embedding FROM memories ORDER BY id").fetchall()
            self._ids = [r[0] for r in rows]
            self._who = [frozenset(json.loads(r[1])) for r in rows]
            if rows:
                self._vecs = np.stack(
                    [np.frombuffer(r[2], dtype=np.float32) for r in rows]
                ).astype(np.float16)
            else:
                self._vecs = np.zeros((0, 384), dtype=np.float16)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def check(self) -> bool:
        with self._lock:
            return self._db.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    # ── sessions ──────────────────────────────────────────────────────────
    def open_session(self, users: Iterable[str], *, idle_s: float, now: float | None = None) -> Session:
        """Reuse the latest session for exactly these people if it was active
        within ``idle_s``; otherwise start a new one."""
        now = time.time() if now is None else now
        who = people(users)
        key = json.dumps(who)
        with self._tx() as db:
            row = db.execute(
                "SELECT id, started, last_active FROM sessions WHERE participants=? "
                "ORDER BY last_active DESC LIMIT 1",
                (key,),
            ).fetchone()
            if row and now - row[2] <= idle_s:
                db.execute("UPDATE sessions SET last_active=? WHERE id=?", (now, row[0]))
                return Session(row[0], who, row[1], now, is_new=False)
            sid = f"s-{int(now * 1000):x}-{secrets.token_hex(4)}"
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (sid, key, now, now))
            return Session(sid, who, now, now, is_new=True)

    # ── conversation log ──────────────────────────────────────────────────
    def log(self, session: Session, role: str, speaker: str, content: str,
            *, turn_id: str | None = None, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._tx() as db:
            db.execute(
                "INSERT INTO messages(session_id, turn_id, role, speaker, content, ts) VALUES (?,?,?,?,?,?)",
                (session.id, turn_id, role, speaker, content, now),
            )
            db.execute("UPDATE sessions SET last_active=? WHERE id=?", (now, session.id))

    def history(self, session: Session, limit_turns: int) -> list[Message]:
        with self._lock:
            rows = self._db.execute(
                "SELECT role, speaker, content, ts FROM messages WHERE session_id=? "
                "ORDER BY id DESC LIMIT ?",
                (session.id, max(0, limit_turns) * 2),
            ).fetchall()
        return [Message(*r) for r in reversed(rows)]

    # ── long-term memory ──────────────────────────────────────────────────
    @staticmethod
    def worth_remembering(text: str) -> float:
        """0 = don't store; otherwise an importance in (0, 1]."""
        stripped = text.strip()
        if len(stripped) < 12 or _FILLER.match(stripped):
            return 0.0
        explicit = bool(_EXPLICIT.search(stripped)) and not stripped.lower().startswith(("do you remember", "remind me"))
        if not explicit and (stripped.endswith("?") or _QUESTION.match(stripped)):
            return 0.0
        importance = 0.5
        if explicit:
            importance = 0.9
        elif len(stripped) > 80:
            importance = 0.6
        return importance

    def remember(self, session: Session, speaker: str, text: str,
                 *, importance: float | None = None, now: float | None = None) -> int | None:
        """Store what ``speaker`` said, heard by everyone in ``session``.
        Returns the memory id, or None if it wasn't worth storing.
        A near-duplicate heard by the same people just refreshes its timestamp."""
        now = time.time() if now is None else now
        imp = self.worth_remembering(text) if importance is None else importance
        if imp <= 0:
            return None
        vec = np.asarray(self.encoder.encode(text.strip()), dtype=np.float32)
        who = frozenset(session.participants)
        with self._tx() as db:
            if len(self._ids):
                sims = self._vecs.astype(np.float32) @ vec
                for idx in np.argsort(-sims)[:5]:
                    if sims[idx] < self.dedupe_threshold:
                        break
                    if self._who[idx] == who:
                        mid = self._ids[idx]
                        db.execute(
                            "UPDATE memories SET ts=?, importance=MAX(importance, ?), text=? WHERE id=?",
                            (now, imp, text.strip(), mid),
                        )
                        return mid
            cur = db.execute(
                "INSERT INTO memories(speaker, participants, session_id, text, ts, importance, embedding) "
                "VALUES (?,?,?,?,?,?,?)",
                (speaker, json.dumps(sorted(who)), session.id, text.strip(), now, imp, vec.tobytes()),
            )
            mid = int(cur.lastrowid)
            self._ids.append(mid)
            self._who.append(who)
            self._vecs = np.vstack([self._vecs, vec.astype(np.float16)[None, :]])
            return mid

    def recall(self, session: Session, query: str, *, k: int, min_score: float,
               now: float | None = None) -> list[Memory]:
        """Best ``k`` memories visible to ``session`` for ``query``."""
        now = time.time() if now is None else now
        if not query.strip() or not self._ids:
            return []
        q = np.asarray(self.encoder.encode(query.strip()), dtype=np.float32)
        present = frozenset(session.participants)
        with self._lock:
            visible = np.fromiter((present <= who for who in self._who), dtype=bool, count=len(self._who))
            if not visible.any():
                return []
            sims = self._vecs.astype(np.float32) @ q
            sims[~visible] = -1.0
            order = [int(i) for i in np.argsort(-sims)[: k * 3] if sims[i] >= min_score]
            if not order:
                return []
            ids = [self._ids[i] for i in order]
            marks = ",".join("?" * len(ids))
            rows = {
                r[0]: r
                for r in self._db.execute(
                    f"SELECT id, speaker, participants, text, ts, importance FROM memories WHERE id IN ({marks})",
                    ids,
                ).fetchall()
            }
        scored: list[Memory] = []
        for i in order:
            mid = self._ids[i]
            r = rows.get(mid)
            if r is None:
                continue
            age_days = max(0.0, (now - r[4]) / 86400.0)
            # Similarity dominates; importance and recency only break near-ties.
            score = float(sims[i]) + 0.05 * float(r[5]) + 0.03 / (1.0 + age_days / 30.0)
            scored.append(Memory(mid, r[1], tuple(json.loads(r[2])), r[3], r[4], score))
        scored.sort(key=lambda m: m.score, reverse=True)
        best = scored[:k]
        if best:
            with self._tx() as db:
                db.executemany("UPDATE memories SET last_recalled=? WHERE id=?", [(now, m.id) for m in best])
        return best

    def forget(self, memory_ids: Sequence[int]) -> int:
        if not memory_ids:
            return 0
        with self._tx() as db:
            marks = ",".join("?" * len(memory_ids))
            n = db.execute(f"DELETE FROM memories WHERE id IN ({marks})", list(memory_ids)).rowcount
        self._load_index()
        return n

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "memories": self._db.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
                "messages": self._db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                "sessions": self._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
            }

    def backup(self, dest: Path) -> Path:
        """Consistent online copy (same as sqlite3 .backup), integrity-checked."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            target = sqlite3.connect(str(dest))
            try:
                self._db.backup(target)
                ok = target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            finally:
                target.close()
        if not ok:
            dest.unlink(missing_ok=True)
            raise RuntimeError(f"backup {dest} failed integrity_check")
        return dest
