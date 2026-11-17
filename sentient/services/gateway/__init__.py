"""API-only gateway for AIRI.

There is no chat page. AIRI is the only operator-facing UI. This package
exposes an OpenAI-compatible chat endpoint and a small set of authenticated
control jobs. It does not run a shell, sudo, nmap, or a camera proxy.
"""

from sentient.services.gateway.app import create_app
from sentient.services.gateway.config import ConfigError, GatewayConfig

__all__ = ["ConfigError", "GatewayConfig", "create_app"]
