#!/usr/bin/env python3
"""
Sentient Core - Memory System HTTP API
FastAPI server that wraps MemorySystem for HTTP access.

Endpoints:
- GET /health: Health check
- POST /recall: Search memories by query
- POST /store: Store new interaction
- GET /stats: Get memory statistics
- GET /context: Get working memory context
- POST /core: Update core memory
- GET /core: Get core memory
"""

import asyncio
import json
import logging
import os
from typing import List, Dict, Optional, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from memory import MemorySystem, Interaction, Memory

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Request/Response Models
class RecallRequest(BaseModel):
    """Request model for memory recall/search."""
    query: str = Field(..., description="Search query text")
    limit: int = Field(5, ge=1, le=50, description="Maximum number of results")
    min_similarity: float = Field(0.5, ge=0.0, le=1.0, description="Minimum similarity threshold")
    tags: Optional[List[str]] = Field(None, description="Filter by tags")


class RecallResponse(BaseModel):
    """Response model for memory recall."""
    memories: List[Dict[str, Any]] = Field(..., description="List of matching memories with similarity scores")


class StoreRequest(BaseModel):
    """Request model for storing interaction."""
    user_msg: str = Field(..., description="User's message")
    assistant_msg: str = Field(..., description="Assistant's response")
    force_episodic: bool = Field(False, description="Force storage in episodic memory")


class StoreResponse(BaseModel):
    """Response model for store operation."""
    status: str = Field(..., description="Operation status")
    interaction_id: str = Field(..., description="Generated interaction ID")
    importance_score: float = Field(..., description="Calculated importance score")
    stored_episodic: bool = Field(..., description="Whether stored in episodic memory")


class CoreMemoryRequest(BaseModel):
    """Request model for updating core memory."""
    key: str = Field(..., description="Memory key (e.g., 'name', 'preferences.music')")
    value: Any = Field(..., description="Memory value (JSON-serializable)")


class CoreMemoryResponse(BaseModel):
    """Response model for core memory operations."""
    status: str = Field(..., description="Operation status")
    key: str = Field(..., description="Memory key")
    value: Any = Field(None, description="Memory value")


class HealthResponse(BaseModel):
    """Response model for health check."""
    status: str = Field(..., description="Service health status")
    redis_connected: bool = Field(..., description="Redis connection status")
    mqtt_connected: bool = Field(..., description="MQTT connection status")


class StatsResponse(BaseModel):
    """Response model for memory statistics."""
    working_memory: Dict[str, Any]
    episodic_memory: Dict[str, Any]
    core_memory: Dict[str, Any]
    embedding_model: int


class ContextResponse(BaseModel):
    """Response model for working context."""
    interactions: List[Dict[str, Any]]


# Global memory system instance
memory_system: Optional[MemorySystem] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - handles startup and shutdown."""
    global memory_system

    # Startup
    logger.info("Starting Memory HTTP API...")

    memory_system = MemorySystem(
        redis_host=os.getenv("REDIS_HOST", "localhost"),
        redis_port=int(os.getenv("REDIS_PORT", "6379")),
        redis_db=int(os.getenv("REDIS_DB", "0")),
        mqtt_broker=os.getenv("MQTT_BROKER", "localhost"),
        mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
        embedding_model=os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    )

    try:
        await memory_system.connect()
        logger.info("Memory HTTP API started successfully")
    except Exception as e:
        logger.error(f"Failed to connect memory system: {e}")
        raise

    yield

    # Shutdown
    logger.info("Shutting down Memory HTTP API...")
    if memory_system:
        await memory_system.disconnect()
    logger.info("Memory HTTP API stopped")


# FastAPI application
app = FastAPI(
    title="Sentient Core Memory API",
    description="HTTP API for three-tier memory system with semantic search",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint.

    Returns service status and connection health.
    """
    if not memory_system:
        raise HTTPException(status_code=503, detail="Memory system not initialized")

    return HealthResponse(
        status="healthy",
        redis_connected=memory_system.redis_client is not None,
        mqtt_connected=memory_system.mqtt_connected
    )


@app.post("/recall", response_model=RecallResponse)
async def recall_memories(request: RecallRequest):
    """
    Search memories by semantic query.

    Performs semantic search through episodic memories using embedding similarity.

    Args:
        request: RecallRequest with query and search parameters

    Returns:
        RecallResponse with matching memories and similarity scores
    """
    if not memory_system:
        raise HTTPException(status_code=503, detail="Memory system not initialized")

    try:
        results = await memory_system.search_memories(
            query=request.query,
            limit=request.limit,
            min_similarity=request.min_similarity,
            tags=request.tags
        )

        # Convert results to serializable format
        memories = []
        for memory_obj, similarity in results:
            memories.append({
                "interaction": {
                    "user_msg": memory_obj.interaction.user_msg,
                    "assistant_msg": memory_obj.interaction.assistant_msg,
                    "timestamp": memory_obj.interaction.timestamp,
                    "importance_score": memory_obj.interaction.importance_score,
                    "interaction_id": memory_obj.interaction.interaction_id
                },
                "tags": memory_obj.tags,
                "similarity": similarity
            })

        return RecallResponse(memories=memories)

    except Exception as e:
        logger.error(f"Error recalling memories: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/store", response_model=StoreResponse)
async def store_interaction(request: StoreRequest):
    """
    Store new interaction in memory system.

    Stores interaction in working memory and optionally episodic memory
    based on importance score or force flag.

    Args:
        request: StoreRequest with user and assistant messages

    Returns:
        StoreResponse with interaction details
    """
    if not memory_system:
        raise HTTPException(status_code=503, detail="Memory system not initialized")

    try:
        interaction = await memory_system.store_interaction(
            user_msg=request.user_msg,
            assistant_msg=request.assistant_msg,
            force_episodic=request.force_episodic
        )

        stored_episodic = (
            request.force_episodic or
            interaction.importance_score >= memory_system.EPISODIC_MIN_IMPORTANCE
        )

        return StoreResponse(
            status="stored",
            interaction_id=interaction.interaction_id,
            importance_score=interaction.importance_score,
            stored_episodic=stored_episodic
        )

    except Exception as e:
        logger.error(f"Error storing interaction: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/context", response_model=ContextResponse)
async def get_working_context(limit: int = 20):
    """
    Get working memory context (recent interactions).

    Args:
        limit: Maximum number of recent interactions to return (default: 20)

    Returns:
        ContextResponse with list of recent interactions
    """
    if not memory_system:
        raise HTTPException(status_code=503, detail="Memory system not initialized")

    try:
        interactions = await memory_system.get_working_context(limit=limit)

        # Convert to serializable format
        interactions_data = [
            {
                "user_msg": interaction.user_msg,
                "assistant_msg": interaction.assistant_msg,
                "timestamp": interaction.timestamp,
                "importance_score": interaction.importance_score,
                "interaction_id": interaction.interaction_id
            }
            for interaction in interactions
        ]

        return ContextResponse(interactions=interactions_data)

    except Exception as e:
        logger.error(f"Error getting working context: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/core", response_model=CoreMemoryResponse)
async def update_core_memory(request: CoreMemoryRequest):
    """
    Update core memory (facts about Jack).

    Args:
        request: CoreMemoryRequest with key and value

    Returns:
        CoreMemoryResponse with operation status
    """
    if not memory_system:
        raise HTTPException(status_code=503, detail="Memory system not initialized")

    try:
        await memory_system.update_core_memory(
            key=request.key,
            value=request.value
        )

        return CoreMemoryResponse(
            status="updated",
            key=request.key,
            value=request.value
        )

    except Exception as e:
        logger.error(f"Error updating core memory: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/core", response_model=CoreMemoryResponse)
async def get_core_memory(key: Optional[str] = None):
    """
    Get core memory.

    Args:
        key: Specific key to retrieve (optional, returns all if not specified)

    Returns:
        CoreMemoryResponse with memory value(s)
    """
    if not memory_system:
        raise HTTPException(status_code=503, detail="Memory system not initialized")

    try:
        value = await memory_system.get_core_memory(key=key)

        return CoreMemoryResponse(
            status="retrieved",
            key=key or "all",
            value=value
        )

    except Exception as e:
        logger.error(f"Error getting core memory: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats", response_model=StatsResponse)
async def get_memory_stats():
    """
    Get memory system statistics.

    Returns:
        StatsResponse with statistics for all memory tiers
    """
    if not memory_system:
        raise HTTPException(status_code=503, detail="Memory system not initialized")

    try:
        stats = await memory_system.get_memory_stats()
        return StatsResponse(**stats)

    except Exception as e:
        logger.error(f"Error getting memory stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/consolidate")
async def consolidate_memories():
    """
    Trigger memory consolidation process.

    Analyzes recent episodic memories to extract patterns and insights.
    """
    if not memory_system:
        raise HTTPException(status_code=503, detail="Memory system not initialized")

    try:
        await memory_system.consolidate_memories()
        return {"status": "consolidation_complete"}

    except Exception as e:
        logger.error(f"Error consolidating memories: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/working")
async def clear_working_memory():
    """
    Clear working memory.

    Removes all interactions from working memory (volatile tier).
    """
    if not memory_system:
        raise HTTPException(status_code=503, detail="Memory system not initialized")

    try:
        await memory_system.clear_working_memory()
        return {"status": "working_memory_cleared"}

    except Exception as e:
        logger.error(f"Error clearing working memory: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    # Run server
    uvicorn.run(
        "memory_http:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
        log_level="info"
    )
