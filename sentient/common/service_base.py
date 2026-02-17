"""Base class for all Sentient Core services.

Provides:
- MQTT client lifecycle (connect, reconnect, graceful disconnect)
- Optional FastAPI HTTP server with /health endpoint
- Structured logging
- Graceful shutdown on SIGTERM/SIGINT
- Central config loading
"""

import asyncio
import json
import signal
import logging
from typing import Optional, Dict, Any, List, Callable, Awaitable
from contextlib import asynccontextmanager

from sentient.config import get_config, SentientConfig
from sentient.common.logging import setup_logging

try:
    from aiomqtt import Client as MQTTClient, MqttError
except ImportError:
    MQTTClient = None
    MqttError = Exception

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
except ImportError:
    FastAPI = None


class SentientService:
    """Base class for all Sentient Core microservices."""

    def __init__(self, name: str, http_port: Optional[int] = None):
        self.name = name
        self.http_port = http_port
        self.config: SentientConfig = get_config()
        self.logger = setup_logging(name)
        self._mqtt_client: Optional[MQTTClient] = None
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._mqtt_handlers: Dict[str, Callable] = {}
        self._app: Optional[Any] = None  # FastAPI app

    # --- MQTT ---

    def on_mqtt(self, topic: str):
        """Decorator to register an MQTT topic handler."""
        def decorator(func: Callable[[str, bytes], Awaitable[None]]):
            self._mqtt_handlers[topic] = func
            return func
        return decorator

    async def mqtt_publish(self, topic: str, payload: Any):
        """Publish a message to an MQTT topic."""
        if self._mqtt_client is None:
            self.logger.warning(f"MQTT not connected, cannot publish to {topic}")
            return
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        if isinstance(payload, str):
            payload = payload.encode()
        await self._mqtt_client.publish(topic, payload)

    async def _mqtt_loop(self):
        """Main MQTT connection loop with auto-reconnect and exponential backoff."""
        cfg = self.config.mqtt
        reconnect_delay = 1  # Start at 1 second
        max_delay = 60  # Cap at 60 seconds
        while self._running:
            try:
                async with MQTTClient(
                    hostname=cfg.broker,
                    port=cfg.port,
                    username=cfg.username or None,
                    password=cfg.password or None,
                    identifier=f"sentient-{self.name}",
                ) as client:
                    self._mqtt_client = client
                    self.logger.info(f"MQTT connected to {cfg.broker}:{cfg.port}")
                    reconnect_delay = 1  # Reset on successful connection

                    # Subscribe to all registered topics
                    for topic in self._mqtt_handlers:
                        await client.subscribe(topic)
                        self.logger.debug(f"Subscribed to {topic}")

                    # Message dispatch loop
                    async for message in client.messages:
                        topic_str = str(message.topic)
                        for pattern, handler in self._mqtt_handlers.items():
                            # Simple topic matching (exact or wildcard)
                            if topic_str == pattern or _topic_matches(topic_str, pattern):
                                try:
                                    await handler(topic_str, message.payload)
                                except Exception as e:
                                    self.logger.error(f"Handler error for {topic_str}: {e}", exc_info=True)

            except MqttError as e:
                self._mqtt_client = None
                if self._running:
                    self.logger.warning(f"MQTT disconnected: {e}, reconnecting in {reconnect_delay}s...")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, max_delay)
            except Exception as e:
                self._mqtt_client = None
                if self._running:
                    self.logger.error(f"MQTT error: {e}, reconnecting in {reconnect_delay}s...", exc_info=True)
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, max_delay)

    # --- HTTP ---

    def get_app(self) -> "FastAPI":
        """Get or create the FastAPI app."""
        if self._app is None and FastAPI:
            @asynccontextmanager
            async def lifespan(app):
                yield

            self._app = FastAPI(
                title=f"Sentient Core - {self.name.title()} Service",
                lifespan=lifespan,
            )
            self._app.add_middleware(
                CORSMiddleware,
                allow_origins=["*"],  # TODO: restrict in production
                allow_methods=["*"],
                allow_headers=["*"],
            )

            @self._app.get("/health")
            async def health():
                return {
                    "service": self.name,
                    "status": "healthy",
                    "mqtt_connected": self._mqtt_client is not None,
                }
        return self._app

    async def _run_http(self):
        """Run the FastAPI HTTP server."""
        if self.http_port and FastAPI:
            app = self.get_app()
            config = uvicorn.Config(
                app,
                host="0.0.0.0",
                port=self.http_port,
                log_level="warning",
            )
            server = uvicorn.Server(config)
            await server.serve()

    # --- Lifecycle ---

    async def setup(self):
        """Override in subclass for service-specific initialization."""
        pass

    async def teardown(self):
        """Override in subclass for service-specific cleanup."""
        pass

    async def run(self):
        """Main entry point. Starts MQTT, HTTP, and runs until shutdown."""
        self._running = True
        self.logger.info(f"Starting {self.name} service...")

        # Register signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        # Service-specific setup
        await self.setup()

        # Start background tasks
        if MQTTClient:
            self._tasks.append(asyncio.create_task(self._mqtt_loop()))

        if self.http_port:
            self._tasks.append(asyncio.create_task(self._run_http()))

        self.logger.info(f"{self.name} service started")

        # Wait until shutdown
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass
        finally:
            await self.teardown()
            self.logger.info(f"{self.name} service stopped")

    async def shutdown(self):
        """Graceful shutdown."""
        self.logger.info(f"Shutting down {self.name}...")
        self._running = False
        for task in self._tasks:
            task.cancel()


def _topic_matches(actual: str, pattern: str) -> bool:
    """Simple MQTT topic pattern matching with + and # wildcards."""
    if pattern == actual:
        return True
    pattern_parts = pattern.split("/")
    actual_parts = actual.split("/")
    for i, p in enumerate(pattern_parts):
        if p == "#":
            return True
        if i >= len(actual_parts):
            return False
        if p != "+" and p != actual_parts[i]:
            return False
    return len(pattern_parts) == len(actual_parts)
