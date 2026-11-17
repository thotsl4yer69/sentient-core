"""Exact allowlist of control jobs.

status.health and status.avatar only read this process. turn.cancel publishes
one MQTT message. No job type runs a shell, sudo, nmap, systemctl, or a
camera proxy, and unknown types are rejected.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sentient.services.gateway.errors import GatewayError
from sentient.services.gateway.mqtt_bridge import MqttBridge
from sentient.services.gateway.policy import ALLOWED_JOB_TYPES, classify_text, job_type_denied, refusal

_TURN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_NO_ARG_JOBS = frozenset({"status.health", "status.avatar"})


class JobRunner:
    def __init__(self, bridge: MqttBridge | None, health_snapshot) -> None:
        self._bridge = bridge
        self._health_snapshot = health_snapshot
        self._jobs: dict[str, dict[str, Any]] = {}

    def get(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise GatewayError(404, "job_not_found", "No such job.", "invalid_request_error")
        return job

    async def submit(self, job_type: str, args: dict[str, Any] | None) -> dict[str, Any]:
        if job_type not in ALLOWED_JOB_TYPES:
            raise job_type_denied(job_type)
        arguments = dict(args or {})
        self._validate_args(job_type, arguments)
        job_id = "job_" + uuid.uuid4().hex
        try:
            result = await self._run(job_type, arguments)
            record = {"id": job_id, "type": job_type, "status": "completed", "result": result}
        except GatewayError as exc:
            record = {
                "id": job_id,
                "type": job_type,
                "status": "failed",
                "error": {"message": exc.message, "code": exc.code},
            }
            self._jobs[job_id] = record
            raise
        self._jobs[job_id] = record
        return record

    def _validate_args(self, job_type: str, args: dict[str, Any]) -> None:
        if job_type in _NO_ARG_JOBS:
            if args:
                raise GatewayError(400, "job_args", "This job takes no arguments.", "invalid_request_error")
            return
        unknown = set(args) - {"turn_id", "reason"}
        if unknown:
            raise GatewayError(400, "job_args", "turn.cancel only accepts turn_id and reason.", "invalid_request_error")
        turn_id = args.get("turn_id")
        if not isinstance(turn_id, str) or not _TURN_ID.fullmatch(turn_id):
            raise GatewayError(400, "turn_id", "turn_id must be 1 to 64 safe characters.", "invalid_request_error")
        reason = args.get("reason", "operator")
        if not isinstance(reason, str) or not reason or len(reason) > 200 or "\n" in reason or "\r" in reason:
            raise GatewayError(400, "job_args", "reason must be a single line of at most 200 characters.", "invalid_request_error")
        category = classify_text(reason)
        if category:
            raise refusal(category)

    async def _run(self, job_type: str, args: dict[str, Any]) -> dict[str, Any]:
        if job_type == "status.health":
            return self._health_snapshot()
        if job_type == "status.avatar":
            state = self._bridge.avatar_state() if self._bridge is not None else None
            return {"state": state}
        if job_type == "turn.cancel":
            if self._bridge is None or not self._bridge.connected:
                raise GatewayError(503, "mqtt_offline", "MQTT broker is not connected.", "server_error")
            await self._bridge.publish_cancel(args["turn_id"], args.get("reason", "operator"))
            return {"turn_id": args["turn_id"], "cancelled": True}
        raise job_type_denied(job_type)
