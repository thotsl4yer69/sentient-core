"""Runtime configuration. There is no default token and no default MQTT password.

The listener binds to loopback unless SC_GATEWAY_BIND_LAN is set, and a LAN
bind requires TLS. MQTT passwords leave the host only when the broker URL is
the TLS listener.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from sentient.services.gateway.auth import TokenStore, parse_token_file, parse_token_spec

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_TRUE = frozenset({"1", "true", "yes", "on"})


class ConfigError(ValueError):
    """The process must not start with this configuration."""


@dataclass(frozen=True)
class MqttSettings:
    enabled: bool
    host: str
    port: int
    username: str
    password: str
    tls_ca: str | None


@dataclass(frozen=True)
class GatewayConfig:
    host: str
    port: int
    bind_lan: bool
    tls_cert: str | None
    tls_key: str | None
    tokens: TokenStore
    mqtt: MqttSettings
    llm_url: str | None
    model: str
    chat_timeout: float
    cors_origins: frozenset[str]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GatewayConfig":
        source = os.environ if env is None else env
        tokens = _load_tokens(source)
        bind_lan = _flag(source, "SC_GATEWAY_BIND_LAN")
        requested_bind = source.get("SC_GATEWAY_BIND", "127.0.0.1").strip() or "127.0.0.1"
        tls_cert = _optional_path(source, "SC_GATEWAY_TLS_CERT")
        tls_key = _optional_path(source, "SC_GATEWAY_TLS_KEY")
        host = _resolve_bind(requested_bind, bind_lan, tls_cert, tls_key)
        if (tls_cert is None) != (tls_key is None):
            raise ConfigError("TLS cert and key must be set together")
        port = _port(source.get("SC_GATEWAY_PORT", "8443"), "SC_GATEWAY_PORT")
        mqtt = _mqtt_settings(source)
        llm_url = _llm_url(source.get("SC_GATEWAY_LLM_URL", ""), port)
        model = source.get("SC_GATEWAY_MODEL", "local").strip() or "local"
        if not _safe_model(model):
            raise ConfigError("SC_GATEWAY_MODEL contains unsupported characters")
        timeout = _timeout(source.get("SC_GATEWAY_CHAT_TIMEOUT", "60"))
        origins = _origins(source.get("SC_GATEWAY_CORS_ORIGINS", ""))
        return cls(
            host=host,
            port=port,
            bind_lan=bind_lan,
            tls_cert=tls_cert,
            tls_key=tls_key,
            tokens=tokens,
            mqtt=mqtt,
            llm_url=llm_url,
            model=model,
            chat_timeout=timeout,
            cors_origins=origins,
        )


def _flag(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() in _TRUE


def _port(value: str, name: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer port") from exc
    if port < 1 or port > 65535:
        raise ConfigError(f"{name} must be between 1 and 65535")
    return port


def _optional_path(env: Mapping[str, str], name: str) -> str | None:
    raw = env.get(name, "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_file():
        raise ConfigError(f"{name} is not a file")
    return str(path)


def _resolve_bind(requested: str, bind_lan: bool, tls_cert: str | None, tls_key: str | None) -> str:
    loopback = requested in LOOPBACK_HOSTS
    if not bind_lan:
        if not loopback:
            raise ConfigError(
                "Refusing non-loopback bind without SC_GATEWAY_BIND_LAN=1 and TLS"
            )
        return "127.0.0.1" if requested == "localhost" else requested
    if tls_cert is None or tls_key is None:
        raise ConfigError("LAN bind requires SC_GATEWAY_TLS_CERT and SC_GATEWAY_TLS_KEY")
    if loopback:
        return "0.0.0.0"
    return requested


def _load_tokens(env: Mapping[str, str]) -> TokenStore:
    specs: list[str] = []
    raw = env.get("SC_GATEWAY_TOKENS", "").strip()
    if raw:
        specs.append(raw)
    file_name = env.get("SC_GATEWAY_TOKEN_FILE", "").strip()
    file_store: TokenStore | None = None
    if file_name:
        path = Path(file_name)
        if not path.is_file():
            raise ConfigError("SC_GATEWAY_TOKEN_FILE is not a file")
        mode = path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ConfigError("SC_GATEWAY_TOKEN_FILE must not be group or world accessible")
        try:
            file_store = parse_token_file(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
    store: TokenStore | None = None
    if specs:
        try:
            store = parse_token_spec(",".join(specs))
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
    if store and file_store:
        try:
            store = parse_token_spec(
                ",".join(
                    [
                        *specs,
                        _file_spec(file_name),
                    ]
                )
            )
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        return store
    if store:
        return store
    if file_store:
        return file_store
    raise ConfigError("SC_GATEWAY_TOKENS is required; the gateway has no default credential")


def _file_spec(file_name: str) -> str:
    """Re-read a validated token file into the spec form. Mode was already checked."""
    parts: list[str] = []
    for line in Path(file_name).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        scope, sep, token = stripped.partition(" ")
        if not sep:
            scope, sep, token = stripped.partition("\t")
        parts.append(f"{scope}:{token}")
    return ",".join(parts)


def _mqtt_settings(env: Mapping[str, str]) -> MqttSettings:
    enabled = env.get("SC_GATEWAY_MQTT", "1").strip().lower() not in {"0", "false", "no", "off"}
    host = env.get("SC_MQTT_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = _port(env.get("SC_MQTT_PORT", "1883"), "SC_MQTT_PORT")
    username = env.get("SC_MQTT_USER", "sc-gateway").strip() or "sc-gateway"
    password = env.get("SC_MQTT_PASS", "").strip() or env.get("SC_MQTT_PASS_GATEWAY", "").strip()
    tls_ca = _optional_path(env, "SC_MQTT_TLS_CA")
    if not enabled:
        return MqttSettings(False, host, port, username, "", tls_ca)
    if not password:
        raise ConfigError("SC_MQTT_PASS is required when MQTT is enabled; there is no default password")
    if host not in LOOPBACK_HOSTS:
        if port == 1883:
            raise ConfigError("Refusing plaintext MQTT port 1883 off loopback")
        if tls_ca is None:
            raise ConfigError("LAN MQTT requires TLS (SC_MQTT_TLS_CA) on the 8883 listener")
    return MqttSettings(True, host, port, username, password, tls_ca)


def _safe_model(model: str) -> bool:
    if len(model) < 1 or len(model) > 128:
        return False
    return all(char.isalnum() or char in "._:-" for char in model)


def _llm_url(raw: str, gateway_port: int) -> str | None:
    raw = raw.strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError("SC_GATEWAY_LLM_URL must be an http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigError("SC_GATEWAY_LLM_URL must not carry credentials, a query, or a fragment")
    if parsed.hostname not in LOOPBACK_HOSTS:
        raise ConfigError("SC_GATEWAY_LLM_URL must stay on loopback")
    if parsed.port is None:
        raise ConfigError("SC_GATEWAY_LLM_URL must include a port")
    if parsed.port == gateway_port:
        raise ConfigError("SC_GATEWAY_LLM_URL must not point at the gateway itself")
    return raw.rstrip("/")


def _timeout(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError("SC_GATEWAY_CHAT_TIMEOUT must be a number of seconds") from exc
    if value < 1 or value > 60:
        raise ConfigError("SC_GATEWAY_CHAT_TIMEOUT must be from 1 to 60 seconds")
    return value


def _origins(raw: str) -> frozenset[str]:
    origins: set[str] = set()
    for piece in raw.split(","):
        origin = piece.strip()
        if not origin:
            continue
        if origin == "*" or " " in origin or "*" in origin:
            raise ConfigError("SC_GATEWAY_CORS_ORIGINS must be an explicit origin list")
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"}:
            raise ConfigError("SC_GATEWAY_CORS_ORIGINS entries must be origins like https://nanob.lan:8443")
        origins.add(origin.rstrip("/"))
    return frozenset(origins)
