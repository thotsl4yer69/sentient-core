"""
Perception Layer HTTP API - FastAPI Server
Wraps PerceptionLayer to expose world state via HTTP endpoints
"""

import asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sentient.config import get_config
from sentient.common.service_base import SentientService
from sentient.common.logging import setup_logging
from .engine import PerceptionLayer

# Setup logging
logger = setup_logging("perception-api")


# Response models
class HealthResponse(BaseModel):
    status: str


class SystemHealthNode(BaseModel):
    online: bool
    cpu_percent: Optional[float] = None
    memory_percent: Optional[float] = None
    temperature: Optional[float] = None
    uptime: Optional[int] = None


class WorldStateResponse(BaseModel):
    jack_present: bool
    threat_level: int
    ambient_state: str
    time_context: str
    last_interaction_seconds: int
    system_health: dict
    timestamp: str
    jack_location: Optional[str] = None
    active_threats: list = []


class PerceptionService(SentientService):
    """Perception service with HTTP API and PerceptionLayer engine"""

    def __init__(self):
        config = get_config()
        super().__init__(name="perception", http_port=config.perception.port)
        self.perception_layer: Optional[PerceptionLayer] = None
        self.perception_task: Optional[asyncio.Task] = None

    async def setup(self):
        """Initialize perception layer"""
        logger.info("Starting PerceptionLayer...")

        self.perception_layer = PerceptionLayer()

        # Start perception layer in background
        self.perception_task = asyncio.create_task(self.perception_layer.run())

        # Set up HTTP endpoints
        self._setup_routes()

        logger.info(f"Perception API started on port {self.http_port}")

    async def teardown(self):
        """Stop perception layer"""
        logger.info("Stopping PerceptionLayer...")

        if self.perception_layer:
            await self.perception_layer.stop()

        if self.perception_task:
            self.perception_task.cancel()
            try:
                await self.perception_task
            except asyncio.CancelledError:
                pass

    def _setup_routes(self):
        """Set up FastAPI routes"""
        app = self.get_app()

        @app.get("/state", response_model=WorldStateResponse)
        async def get_world_state():
            """
            Get current world state from perception layer

            Returns unified world state including:
            - Jack's presence and location
            - Threat level (0-10)
            - Ambient state (quiet/active/noisy)
            - Time context (morning/afternoon/evening/night)
            - Last interaction time
            - System health for all nodes
            """
            if not self.perception_layer:
                raise HTTPException(
                    status_code=503,
                    detail="Perception layer not initialized"
                )

            try:
                # Build current world state
                world_state = self.perception_layer.build_world_state()

                # Convert to response model
                response = WorldStateResponse(
                    jack_present=world_state.jack_present,
                    jack_location=world_state.jack_location,
                    threat_level=world_state.threat_level,
                    active_threats=world_state.active_threats,
                    ambient_state=world_state.ambient_state,
                    time_context=world_state.time_context,
                    last_interaction_seconds=world_state.last_interaction_seconds,
                    system_health=world_state.system_health,
                    timestamp=world_state.timestamp
                )

                return response

            except Exception as e:
                logger.error(f"Error getting world state: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to get world state: {str(e)}"
                )

        @app.get("/state/simple")
        async def get_simple_state():
            """
            Get simplified world state (minimal data)

            Returns only the most critical state information
            """
            if not self.perception_layer:
                raise HTTPException(
                    status_code=503,
                    detail="Perception layer not initialized"
                )

            try:
                world_state = self.perception_layer.build_world_state()

                return {
                    "jack_present": world_state.jack_present,
                    "threat_level": world_state.threat_level,
                    "ambient_state": world_state.ambient_state,
                    "time_context": world_state.time_context
                }

            except Exception as e:
                logger.error(f"Error getting simple state: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to get simple state: {str(e)}"
                )


async def main():
    """Main entry point"""
    service = PerceptionService()
    await service.run()


if __name__ == "__main__":
    asyncio.run(main())
