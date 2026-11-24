"""Settings for sc-core.

Read from a TOML file (``SC_CONFIG``, default ``/etc/sc/sc.toml``) and then
from ``SC_*`` environment variables, which win. Secrets never live here: bearer
tokens are read from ``tokens_file`` (root-owned, 0600 or 0640).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on the Jetson's Python 3.10
    import tomli as tomllib

DEFAULT_CONFIG_PATH = Path("/etc/sc/sc.toml")
REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    # HTTP API (AIRI and sc-voice both use it). Loopback unless set otherwise.
    bind_host: str = "127.0.0.1"
    bind_port: int = 8770
    # Exact browser origins allowed to call the API (e.g. the AIRI page).
    cors_origins: tuple[str, ...] = ()
    # TLS for anything beyond loopback: bearer tokens must not cross Wi-Fi in
    # clear text. A LAN bind without a cert is refused unless allow_plain_lan.
    tls_cert: Path | None = None
    tls_key: Path | None = None
    allow_plain_lan: bool = False

    # llama.cpp llama-server
    llm_url: str = "http://127.0.0.1:18080"
    llm_model: str = "local"
    llm_connect_timeout_s: float = 3.0

    # Turn deadlines
    voice_queue_s: float = 2.0
    chat_queue_s: float = 30.0
    voice_first_token_s: float = 8.0
    chat_first_token_s: float = 30.0
    voice_total_s: float = 25.0
    chat_total_s: float = 120.0
    voice_max_tokens: int = 160
    chat_max_tokens: int = 1024
    temperature: float = 0.7

    # Storage and identity
    db_path: Path = Path("/var/lib/sc/sc.db")
    tokens_file: Path = Path("/etc/sc/tokens")
    persona_path: Path = REPO_ROOT / "personality" / "cortana.md"
    assistant_name: str = "Cortana"
    timezone: str = "Australia/Melbourne"

    # Conversation and memory
    session_idle_s: float = 1800.0
    history_turns: int = 8
    memory_top_k: int = 6
    memory_min_score: float = 0.35
    models_dir: Path | None = None

    @property
    def lan_exposed(self) -> bool:
        return self.bind_host not in {"127.0.0.1", "::1", "localhost"}


_ENV_PREFIX = "SC_"


def _coerce(name: str, value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int) and not isinstance(default, bool):
        return int(value)
    if isinstance(default, float):
        return float(value)
    if isinstance(default, tuple):
        if isinstance(value, (list, tuple)):
            return tuple(str(v) for v in value)
        return tuple(part.strip() for part in str(value).split(",") if part.strip())
    if isinstance(default, Path) or name in {"models_dir", "tls_cert", "tls_key"}:
        return Path(str(value)).expanduser() if value not in (None, "") else None
    return str(value)


def load_settings(
    path: Path | None = None, env: Mapping[str, str] | None = None
) -> Settings:
    env = os.environ if env is None else env
    config_path = Path(env.get("SC_CONFIG", "")) if env.get("SC_CONFIG") else (path or DEFAULT_CONFIG_PATH)
    values: dict[str, Any] = {}
    if config_path.is_file():
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
        known = {f.name for f in fields(Settings)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown settings in {config_path}: {sorted(unknown)}")
        values.update(data)
    defaults = Settings()
    for f in fields(Settings):
        key = _ENV_PREFIX + f.name.upper()
        if key in env:
            values[f.name] = env[key]
    coerced = {
        name: _coerce(name, value, getattr(defaults, name)) for name, value in values.items()
    }
    settings = Settings(**coerced)
    _validate(settings)
    return settings


def _validate(s: Settings) -> None:
    if not 0 < s.bind_port < 65536:
        raise ValueError("bind_port must be 1-65535")
    if (s.tls_cert is None) != (s.tls_key is None):
        raise ValueError("set both tls_cert and tls_key, or neither")
    if s.lan_exposed and s.tls_cert is None and not s.allow_plain_lan:
        raise ValueError(
            f"bind_host {s.bind_host} is beyond loopback: set tls_cert/tls_key "
            "(deploy/v3/make-cert.sh), or allow_plain_lan = true to accept tokens in clear text"
        )
    for origin in s.cors_origins:
        if origin == "*" or not origin.startswith(("http://", "https://")):
            raise ValueError(f"cors_origins must be exact http(s) origins, got {origin!r}")
    for name in ("voice_queue_s", "chat_queue_s", "voice_first_token_s", "chat_first_token_s",
                 "voice_total_s", "chat_total_s", "session_idle_s"):
        if getattr(s, name) <= 0:
            raise ValueError(f"{name} must be positive")
    if not 0.0 <= s.temperature <= 2.0:
        raise ValueError("temperature must be 0-2")
