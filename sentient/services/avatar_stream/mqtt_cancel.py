"""Subscribe to sc/v1 turn/cancel and stop the matching avatar turn.

Imported lazily so unit tests do not need a broker or paho-mqtt.
"""

from __future__ import annotations

import json
import logging
import ssl
from typing import Any

from sentient.services.avatar_stream.hub import TurnHub

LOG = logging.getLogger("sentient.avatar_stream")


def start_cancel_subscriber(
    hub: TurnHub,
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    topic_prefix: str,
    use_tls: bool,
    ca_file: str = "",
) -> Any:
    """Connect a background MQTT client. Returns the client so the caller can disconnect."""
    import paho.mqtt.client as mqtt

    topic = f"{topic_prefix.strip('/')}/turn/cancel"
    version = getattr(mqtt, "CallbackAPIVersion", None)
    if version is not None:
        client = mqtt.Client(version.VERSION2, client_id="sc-avatar-stream")
    else:
        client = mqtt.Client(client_id="sc-avatar-stream")
    if username:
        client.username_pw_set(username, password)
    if use_tls:
        client.tls_set(ca_certs=ca_file or None, cert_reqs=ssl.CERT_REQUIRED)

    def on_connect(client: Any, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any = None) -> None:
        if getattr(reason_code, "is_failure", False) or (isinstance(reason_code, int) and reason_code != 0):
            LOG.error("mqtt connect failed: %s", reason_code)
            return
        client.subscribe(topic, qos=1)
        LOG.info("subscribed %s", topic)

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            LOG.info("ignored non-json cancel payload")
            return
        if not isinstance(payload, dict):
            return
        turn_id = payload.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id:
            return
        reason = payload.get("reason")
        hub.cancel(turn_id, reason if isinstance(reason, str) else "barge-in")

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect_async(host, port, keepalive=30)
    client.loop_start()
    return client
