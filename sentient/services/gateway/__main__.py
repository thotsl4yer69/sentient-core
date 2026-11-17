"""Run sc-gateway. Tokens and the MQTT password come from the environment."""

from __future__ import annotations

import sys

import uvicorn

from sentient.services.gateway.app import create_app
from sentient.services.gateway.config import ConfigError, GatewayConfig


def main() -> None:
    try:
        config = GatewayConfig.from_env()
    except ConfigError as exc:
        print(f"sc-gateway: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        ssl_certfile=config.tls_cert,
        ssl_keyfile=config.tls_key,
        log_level="info",
        access_log=True,
        proxy_headers=False,
        forwarded_allow_ips="",
    )


if __name__ == "__main__":
    main()
