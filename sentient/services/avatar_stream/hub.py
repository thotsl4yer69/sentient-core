"""In-memory turn hub. Cancel drops a turn and notifies displays immediately."""

from __future__ import annotations

import threading
import time
from typing import Callable

from sentient.services.avatar_stream.package import TurnPackage

CANCEL_DEADLINE_MS = 150


class TurnHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._turns: dict[str, TurnPackage] = {}
        self._cancelled: set[str] = set()
        self._listeners: list[Callable[[dict[str, object]], None]] = []

    def subscribe(self, listener: Callable[[dict[str, object]], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[dict[str, object]], None]) -> None:
        with self._lock:
            self._listeners = [item for item in self._listeners if item is not listener]

    def submit(self, package: TurnPackage) -> bool:
        """Queue a turn for displays. A turn cancelled already is dropped."""
        with self._lock:
            if package.turn_id in self._cancelled:
                return False
            self._turns[package.turn_id] = package
            listeners = list(self._listeners)
        message = {"type": "turn", "package": package}
        for listener in listeners:
            listener(message)
        return True

    def cancel(self, turn_id: str, reason: str = "barge-in") -> float:
        """Mark the turn cancelled and notify listeners. Returns elapsed milliseconds."""
        if not turn_id:
            raise ValueError("turn_id is required")
        started = time.perf_counter()
        with self._lock:
            self._cancelled.add(turn_id)
            self._turns.pop(turn_id, None)
            listeners = list(self._listeners)
        message = {"type": "cancel", "turn_id": turn_id, "reason": reason}
        for listener in listeners:
            listener(message)
        return (time.perf_counter() - started) * 1000.0

    def is_cancelled(self, turn_id: str) -> bool:
        with self._lock:
            return turn_id in self._cancelled

    def active(self, turn_id: str) -> TurnPackage | None:
        with self._lock:
            return self._turns.get(turn_id)
