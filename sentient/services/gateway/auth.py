"""Bearer-token authentication for the gateway.

Tokens are compared by SHA-256 digest so a length mismatch cannot raise and
every configured token is checked. The token value is not kept on the
principal and is never written to a log line from this module.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass

from sentient.services.gateway.errors import GatewayError

_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{16,256}")
_SCOPES = frozenset({"chat", "control"})


@dataclass(frozen=True)
class Principal:
    scopes: frozenset[str]


@dataclass(frozen=True)
class TokenStore:
    entries: tuple[tuple[bytes, frozenset[str]], ...]

    def match(self, presented: str) -> Principal | None:
        digest = hashlib.sha256(presented.encode("utf-8")).digest()
        found: Principal | None = None
        for token_digest, scopes in self.entries:
            if hmac.compare_digest(digest, token_digest):
                found = Principal(scopes)
        return found


def parse_token_spec(spec: str) -> TokenStore:
    entries: list[tuple[bytes, frozenset[str]]] = []
    seen: set[bytes] = set()
    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        scopes_s, sep, token = part.partition(":")
        if not sep:
            raise ValueError("token spec must look like chat:<token> or control:<token>")
        scopes = frozenset(piece for piece in scopes_s.split("+") if piece)
        if not scopes or not scopes <= _SCOPES:
            raise ValueError("token scopes must be chat, control, or chat+control")
        if not _TOKEN.fullmatch(token):
            raise ValueError("token must be 16 to 256 URL-safe characters")
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        if digest in seen:
            raise ValueError("duplicate bearer token")
        seen.add(digest)
        entries.append((digest, scopes))
    if not entries:
        raise ValueError("at least one bearer token is required")
    return TokenStore(tuple(entries))


def parse_token_file(text: str) -> TokenStore:
    parts: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        scope, sep, token = stripped.partition(" ")
        if not sep:
            scope, sep, token = stripped.partition("\t")
        if not sep:
            raise ValueError("token file lines must be '<scopes> <token>'")
        parts.append(f"{scope}:{token}")
    return parse_token_spec(",".join(parts))


def bearer_token(authorization: str | None) -> str | None:
    if authorization is None or not authorization.strip():
        return None
    scheme, sep, value = authorization.strip().partition(" ")
    if not sep or scheme.lower() != "bearer":
        raise GatewayError(401, "invalid_token", "Invalid bearer token.", "authentication_error")
    token = value.strip()
    if not token or " " in token:
        raise GatewayError(401, "invalid_token", "Invalid bearer token.", "authentication_error")
    return token


def query_has_credential(query_keys: set[str]) -> bool:
    blocked = {"token", "access_token", "api_key", "apikey", "key"}
    return any(key.lower() in blocked for key in query_keys)
