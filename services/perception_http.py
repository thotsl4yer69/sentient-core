"""
Perception Layer HTTP API - FastAPI Server
Wraps PerceptionLayer to expose world state via HTTP endpoints
"""

import asyncio
import logging
import os
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import PerceptionLayer
from perception import PerceptionLayer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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


# Global perception layer instance
perception_layer: Optional[PerceptionLayer] = None
perception_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    global perception_layer, perception_task

    # Startup
    logger.info("Starting PerceptionLayer HTTP API...")

    perception_layer = PerceptionLayer(
        mqtt_broker=os.getenv("MQTT_BROKER", "localhost"),
        mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
        mqtt_username=os.getenv("MQTT_USERNAME"),
        mqtt_password=os.getenv("MQTT_PASSWORD"),
        publish_interval=5.0
    )

    # Start perception layer in background
    perception_task = asyncio.create_task(perception_layer.run())

    logger.info("PerceptionLayer HTTP API started on port 8003")

    yield

    # Shutdown
    logger.info("Stopping PerceptionLayer...")
    await perception_layer.stop()

    if perception_task:
        perception_task.cancel()
        try:
            await perception_task
        except asyncio.CancelledError:
            pass

    logger.info("PerceptionLayer HTTP API stopped")


# FastAPI application
app = FastAPI(
    title="Perception Layer API",
    description="HTTP API for Sentient Core Perception Layer",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify allowed origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return HealthResponse(status="healthy")


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
    if not perception_layer:
        raise HTTPException(
            status_code=503,
            detail="Perception layer not initialized"
        )

    try:
        # Build current world state
        world_state = perception_layer.build_world_state()

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
    if not perception_layer:
        raise HTTPException(
            status_code=503,
            detail="Perception layer not initialized"
        )

    try:
        world_state = perception_layer.build_world_state()

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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "perception_http:app",
        host="0.0.0.0",
        port=8003,
        reload=False,
        log_level="info"
    )
