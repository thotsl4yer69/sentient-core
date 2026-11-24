"""sc-avatar-stream process entry. Tokens come from the environment, not argv."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from sentient.services.avatar_stream.mqtt_cancel import start_cancel_subscriber
from sentient.services.avatar_stream.server import AvatarStreamServer, StreamConfig

LOG = logging.getLogger("sentient.avatar_stream")


def config_from_env() -> StreamConfig:
    display = os.environ.get("SC_AVATAR_STREAM_TOKEN", "")
    ingest = os.environ.get("SC_AVATAR_INGEST_TOKEN", "")
    if not display or not ingest:
        raise SystemExit("SC_AVATAR_STREAM_TOKEN and SC_AVATAR_INGEST_TOKEN are required")
    return StreamConfig(
        bind=os.environ.get("SC_AVATAR_STREAM_BIND", "0.0.0.0"),
        port=int(os.environ.get("SC_AVATAR_STREAM_PORT", "8077")),
        display_token=display,
        ingest_token=ingest,
        tls_cert=os.environ.get("SC_AVATAR_TLS_CERT", ""),
        tls_key=os.environ.get("SC_AVATAR_TLS_KEY", ""),
    )


async def _run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    config = config_from_env()
    server = AvatarStreamServer(config)
    mqtt_client = None
    host = os.environ.get("SC_MQTT_HOST", "")
    if host:
        mqtt_client = start_cancel_subscriber(
            server.hub,
            host=host,
            port=int(os.environ.get("SC_MQTT_PORT", "8883")),
            username=os.environ.get("SC_MQTT_USERNAME", ""),
            password=os.environ.get("SC_MQTT_PASSWORD", ""),
            topic_prefix=os.environ.get("SC_TOPIC_PREFIX", "sc/v1"),
            use_tls=os.environ.get("SC_MQTT_TLS", "1") != "0",
            ca_file=os.environ.get("SC_MQTT_CA", ""),
        )
    try:
        await server.serve_forever()
    finally:
        if mqtt_client is not None:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
