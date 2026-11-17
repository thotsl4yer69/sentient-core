"""Shared-secret checks for the avatar stream. Fail closed."""

from __future__ import annotations

import hmac


def token_matches(presented: object, expected: str) -> bool:
    """True only when both sides are non-empty and compare equal in constant time."""
    if not isinstance(presented, str) or not isinstance(expected, str):
        return False
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
