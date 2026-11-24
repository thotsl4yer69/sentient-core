"""Per-person bearer tokens.

Token file (``/etc/sc/tokens``, root-owned, not group/world writable), one per line::

    # user   scopes        token
    jack     chat          <32+ random URL-safe characters>
    partner  chat          <...>
    voice    chat+speaker  <...>   # sc-voice: may say which user is speaking

``chat`` may talk as the token's own user. ``speaker`` may additionally name the
speaking user(s) per request (only sc-voice should hold it). Tokens are compared
by SHA-256 digest with ``hmac.compare_digest`` across every entry, and the raw
token is never stored on the principal or logged.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{32,256}")
_USER = re.compile(r"[a-z][a-z0-9_-]{0,31}")
SCOPES = frozenset({"chat", "speaker", "admin"})


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Principal:
    user: str
    scopes: frozenset[str]

    def can(self, scope: str) -> bool:
        return scope in self.scopes


@dataclass(frozen=True)
class TokenStore:
    entries: tuple[tuple[bytes, Principal], ...]

    def match(self, presented: str) -> Principal | None:
        digest = hashlib.sha256(presented.encode("utf-8")).digest()
        found: Principal | None = None
        for token_digest, principal in self.entries:
            if hmac.compare_digest(digest, token_digest):
                found = principal
        return found

    @property
    def users(self) -> frozenset[str]:
        """People, i.e. principals that aren't service accounts (``speaker``)."""
        return frozenset(p.user for _, p in self.entries if not p.can("speaker"))


def parse_tokens(text: str) -> TokenStore:
    entries: list[tuple[bytes, Principal]] = []
    seen: set[bytes] = set()
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            raise ValueError(f"tokens line {lineno}: expected '<user> <scopes> <token>'")
        user, scopes_s, token = parts
        if not _USER.fullmatch(user):
            raise ValueError(f"tokens line {lineno}: user must be lowercase letters, digits, _ or -")
        scopes = frozenset(s for s in scopes_s.split("+") if s)
        if not scopes or not scopes <= SCOPES:
            raise ValueError(f"tokens line {lineno}: scopes must be from {sorted(SCOPES)} joined by +")
        if not _TOKEN.fullmatch(token):
            raise ValueError(f"tokens line {lineno}: token must be 32-256 URL-safe characters")
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        if digest in seen:
            raise ValueError(f"tokens line {lineno}: duplicate token")
        seen.add(digest)
        entries.append((digest, Principal(user, scopes)))
    if not entries:
        raise ValueError("token file has no tokens")
    return TokenStore(tuple(entries))


def load_tokens(path: Path) -> TokenStore:
    st = os.stat(path)
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_IROTH):
        raise PermissionError(f"{path} must not be world-readable or group/world-writable")
    return parse_tokens(path.read_text(encoding="utf-8"))


def bearer(authorization: str | None) -> str:
    if not authorization or not authorization.strip():
        raise AuthError(401, "missing_token", "A bearer token is required.")
    scheme, _, value = authorization.strip().partition(" ")
    token = value.strip()
    if scheme.lower() != "bearer" or not token or " " in token:
        raise AuthError(401, "invalid_token", "Invalid bearer token.")
    return token


def authenticate(store: TokenStore, authorization: str | None) -> Principal:
    principal = store.match(bearer(authorization))
    if principal is None:
        raise AuthError(401, "invalid_token", "Invalid bearer token.")
    return principal
