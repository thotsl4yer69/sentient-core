"""Structured logging for Sentient Core services."""

import logging
import json
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Format log records as JSON for structured logging."""

    def format(self, record):
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service_name", record.name),
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_data["exception"] = self.formatException(record.exc_info)
        # Add any extra fields
        for key in ("correlation_id", "topic", "endpoint", "duration_ms"):
            if hasattr(record, key):
                log_data[key] = getattr(record, key)
        return json.dumps(log_data)


def setup_logging(service_name: str, level: int = logging.INFO, json_output: bool = False) -> logging.Logger:
    """Set up logging for a service.

    Args:
        service_name: Name of the service (e.g., "memory", "contemplation")
        level: Logging level
        json_output: If True, use JSON format. If False, use human-readable format.

    Returns:
        Configured logger
    """
    logger = logging.getLogger(f"sentient.{service_name}")
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)

        if json_output:
            handler.setFormatter(JSONFormatter())
        else:
            handler.setFormatter(logging.Formatter(
                f"%(asctime)s [{service_name}] %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            ))

        logger.addHandler(handler)

    return logger
