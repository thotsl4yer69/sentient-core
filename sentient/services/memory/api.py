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
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from sentient.config import get_config
from sentient.common.service_base import SentientService
from sentient.common.logging import setup_logging
from .engine import MemorySystem, Interaction, Memory

logger = setup_logging("memory-api")


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
    embedding_cache: Optional[Dict[str, Any]] = None


class ContextResponse(BaseModel):
    """Response model for working context."""
    interactions: List[Dict[str, Any]]


class MemoryService(SentientService):
    """Memory service with FastAPI HTTP interface."""

    def __init__(self):
        cfg = get_config()
        super().__init__(name="memory", http_port=cfg.memory.port)
        self.memory_system: Optional[MemorySystem] = None

    async def setup(self):
        """Initialize memory system and FastAPI routes."""
        # Create memory system
        self.memory_system = MemorySystem()
        await self.memory_system.connect()
        self.logger.info("Memory system connected")

        # Setup FastAPI routes
        app = self.get_app()
        self._register_routes(app)

    async def teardown(self):
        """Cleanup memory system."""
        if self.memory_system:
            await self.memory_system.disconnect()

    def _register_routes(self, app: FastAPI):
        """Register all FastAPI routes."""

        @app.get("/health", response_model=HealthResponse)
        async def health_check():
            """
            Health check endpoint.

            Returns service status and connection health.
            """
            if not self.memory_system:
                raise HTTPException(status_code=503, detail="Memory system not initialized")

            return HealthResponse(
                status="healthy",
                redis_connected=self.memory_system.redis_client is not None,
                mqtt_connected=self._mqtt_client is not None
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
            if not self.memory_system:
                raise HTTPException(status_code=503, detail="Memory system not initialized")

            try:
                results = await self.memory_system.search_memories(
                    query=request.query,
                    limit=request.limit,
                    min_similarity=request.min_similarity,
                    tags=request.tags,
                    mqtt_client=self._mqtt_client
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
                self.logger.error(f"Error recalling memories: {e}")
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
            if not self.memory_system:
                raise HTTPException(status_code=503, detail="Memory system not initialized")

            try:
                interaction = await self.memory_system.store_interaction(
                    user_msg=request.user_msg,
                    assistant_msg=request.assistant_msg,
                    force_episodic=request.force_episodic,
                    mqtt_client=self._mqtt_client
                )

                stored_episodic = (
                    request.force_episodic or
                    interaction.importance_score >= self.memory_system.EPISODIC_MIN_IMPORTANCE
                )

                return StoreResponse(
                    status="stored",
                    interaction_id=interaction.interaction_id,
                    importance_score=interaction.importance_score,
                    stored_episodic=stored_episodic
                )

            except Exception as e:
                self.logger.error(f"Error storing interaction: {e}")
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
            if not self.memory_system:
                raise HTTPException(status_code=503, detail="Memory system not initialized")

            try:
                interactions = await self.memory_system.get_working_context(limit=limit)

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
                self.logger.error(f"Error getting working context: {e}")
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
            if not self.memory_system:
                raise HTTPException(status_code=503, detail="Memory system not initialized")

            try:
                await self.memory_system.update_core_memory(
                    key=request.key,
                    value=request.value,
                    mqtt_client=self._mqtt_client
                )

                return CoreMemoryResponse(
                    status="updated",
                    key=request.key,
                    value=request.value
                )

            except Exception as e:
                self.logger.error(f"Error updating core memory: {e}")
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
            if not self.memory_system:
                raise HTTPException(status_code=503, detail="Memory system not initialized")

            try:
                value = await self.memory_system.get_core_memory(key=key)

                return CoreMemoryResponse(
                    status="retrieved",
                    key=key or "all",
                    value=value
                )

            except Exception as e:
                self.logger.error(f"Error getting core memory: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/stats", response_model=StatsResponse)
        async def get_memory_stats():
            """
            Get memory system statistics.

            Returns:
                StatsResponse with statistics for all memory tiers
            """
            if not self.memory_system:
                raise HTTPException(status_code=503, detail="Memory system not initialized")

            try:
                stats = await self.memory_system.get_memory_stats()
                return StatsResponse(**stats)

            except Exception as e:
                self.logger.error(f"Error getting memory stats: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/history/recent")
        async def get_recent_history(page: int = 1, limit: int = 20):
            """
            Paginated reverse-chronological conversation history.

            Uses in-memory metadata cache for fast access.

            Args:
                page: Page number (1-indexed, default 1)
                limit: Items per page (default 20)

            Returns:
                Paginated list of conversations with metadata.
            """
            if not self.memory_system:
                raise HTTPException(status_code=503, detail="Memory system not initialized")

            try:
                metadata = self.memory_system._memory_metadata
                memory_ids = self.memory_system._memory_ids

                # Build list sorted by timestamp descending
                entries = []
                for mid in memory_ids:
                    meta = metadata.get(mid)
                    if not meta:
                        continue
                    interaction = meta.get("interaction", {})
                    entries.append({
                        "id": mid,
                        "user_msg": interaction.get("user_msg", ""),
                        "assistant_msg": interaction.get("assistant_msg", ""),
                        "timestamp": interaction.get("timestamp", ""),
                        "importance_score": interaction.get("importance_score", 0.0),
                        "tags": meta.get("tags", []),
                    })

                # Sort by timestamp descending (most recent first)
                entries.sort(key=lambda x: x["timestamp"], reverse=True)

                total = len(entries)
                total_pages = max(1, (total + limit - 1) // limit)
                start = (page - 1) * limit
                end = start + limit
                page_entries = entries[start:end]

                return {
                    "conversations": page_entries,
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "total_pages": total_pages,
                }

            except Exception as e:
                self.logger.error(f"Error getting recent history: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/history/search")
        async def search_history(q: str, limit: int = 20):
            """
            Text substring search through cached metadata.

            Searches user_msg and assistant_msg case-insensitively.

            Args:
                q: Search query string
                limit: Maximum results to return (default 20)

            Returns:
                Matching conversations with metadata.
            """
            if not self.memory_system:
                raise HTTPException(status_code=503, detail="Memory system not initialized")

            try:
                metadata = self.memory_system._memory_metadata
                query_lower = q.lower()

                results = []
                for mid, meta in metadata.items():
                    interaction = meta.get("interaction", {})
                    user_msg = interaction.get("user_msg", "")
                    assistant_msg = interaction.get("assistant_msg", "")

                    if query_lower in user_msg.lower() or query_lower in assistant_msg.lower():
                        results.append({
                            "id": mid,
                            "user_msg": user_msg,
                            "assistant_msg": assistant_msg,
                            "timestamp": interaction.get("timestamp", ""),
                            "importance_score": interaction.get("importance_score", 0.0),
                            "tags": meta.get("tags", []),
                        })

                # Sort by timestamp descending
                results.sort(key=lambda x: x["timestamp"], reverse=True)

                total_matches = len(results)
                results = results[:limit]

                return {
                    "query": q,
                    "results": results,
                    "total_matches": total_matches,
                }

            except Exception as e:
                self.logger.error(f"Error searching history: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/history/stats")
        async def get_history_stats():
            """
            Timeline statistics for conversation history.

            Returns interaction counts by day and hour, top tags,
            and date range information.
            """
            if not self.memory_system:
                raise HTTPException(status_code=503, detail="Memory system not initialized")

            try:
                metadata = self.memory_system._memory_metadata

                timestamps = []
                all_tags = []
                for meta in metadata.values():
                    interaction = meta.get("interaction", {})
                    ts = interaction.get("timestamp", "")
                    if ts:
                        timestamps.append(ts)
                    tags = meta.get("tags", [])
                    all_tags.extend(tags)

                total_interactions = len(timestamps)

                if not timestamps:
                    return {
                        "total_interactions": 0,
                        "first_interaction": None,
                        "last_interaction": None,
                        "relationship_days": 0,
                        "interactions_by_day": {},
                        "interactions_by_hour": {},
                        "top_tags": {},
                    }

                timestamps.sort()
                first_interaction = timestamps[0]
                last_interaction = timestamps[-1]

                # Calculate relationship days (timestamps are epoch floats)
                try:
                    first_dt = datetime.fromtimestamp(float(first_interaction))
                    now_dt = datetime.now()
                    relationship_days = max(1, (now_dt - first_dt).days + 1)
                except (ValueError, TypeError):
                    relationship_days = 1

                # Interactions by day (last 365 days)
                cutoff = datetime.now() - timedelta(days=365)
                interactions_by_day = Counter()
                interactions_by_hour = Counter()

                for ts in timestamps:
                    try:
                        dt = datetime.fromtimestamp(float(ts))
                        interactions_by_hour[dt.hour] += 1
                        if dt >= cutoff:
                            day_str = dt.strftime("%Y-%m-%d")
                            interactions_by_day[day_str] += 1
                    except (ValueError, TypeError):
                        continue

                # Ensure all 24 hours present
                for h in range(24):
                    if h not in interactions_by_hour:
                        interactions_by_hour[h] = 0

                # Top 20 tags
                tag_counts = Counter(all_tags)
                top_tags = dict(tag_counts.most_common(20))

                return {
                    "total_interactions": total_interactions,
                    "first_interaction": first_interaction,
                    "last_interaction": last_interaction,
                    "relationship_days": relationship_days,
                    "interactions_by_day": dict(interactions_by_day),
                    "interactions_by_hour": dict(interactions_by_hour),
                    "top_tags": top_tags,
                }

            except Exception as e:
                self.logger.error(f"Error getting history stats: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/relationship")
        async def get_relationship_summary():
            """
            Relationship summary with milestones and core facts.

            Returns conversation statistics, core memory facts,
            and milestone achievements.
            """
            if not self.memory_system:
                raise HTTPException(status_code=503, detail="Memory system not initialized")

            try:
                metadata = self.memory_system._memory_metadata

                timestamps = []
                for meta in metadata.values():
                    interaction = meta.get("interaction", {})
                    ts = interaction.get("timestamp", "")
                    if ts:
                        timestamps.append(ts)

                total_conversations = len(timestamps)

                if timestamps:
                    timestamps.sort()
                    first_interaction = timestamps[0]
                    last_interaction = timestamps[-1]
                    try:
                        first_dt = datetime.fromtimestamp(float(first_interaction))
                        now_dt = datetime.now()
                        relationship_days = max(1, (now_dt - first_dt).days + 1)
                    except (ValueError, TypeError):
                        relationship_days = 1
                else:
                    first_interaction = None
                    last_interaction = None
                    relationship_days = 0

                # Core facts
                core_facts = await self.memory_system.get_core_memory(key=None)

                # Milestones
                milestone_thresholds = [1, 10, 50, 100, 500, 1000, 5000, 10000, 25000]
                milestone_labels = {
                    1: "First conversation",
                    10: "Getting to know each other",
                    50: "Building rapport",
                    100: "A hundred conversations",
                    500: "Deep connection",
                    1000: "A thousand memories",
                    5000: "Inseparable",
                    10000: "Ten thousand moments",
                    25000: "Lifelong bond",
                }

                milestones = []
                for threshold in milestone_thresholds:
                    achieved = total_conversations >= threshold
                    milestone = {
                        "count": threshold,
                        "label": milestone_labels[threshold],
                        "achieved": achieved,
                    }
                    # Add timestamp and date for achieved milestones
                    if achieved and len(timestamps) >= threshold:
                        ts = timestamps[threshold - 1]
                        milestone["timestamp"] = ts
                        try:
                            dt = datetime.fromtimestamp(float(ts))
                            milestone["date"] = dt.strftime("%Y-%m-%d")
                        except (ValueError, TypeError):
                            milestone["date"] = None
                    else:
                        milestone["timestamp"] = None
                        milestone["date"] = None

                    milestones.append(milestone)

                return {
                    "total_conversations": total_conversations,
                    "relationship_days": relationship_days,
                    "first_interaction": first_interaction,
                    "last_interaction": last_interaction,
                    "core_facts": core_facts,
                    "milestones": milestones,
                }

            except Exception as e:
                self.logger.error(f"Error getting relationship summary: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/consolidate")
        async def consolidate_memories():
            """
            Trigger memory consolidation process.

            Analyzes recent episodic memories to extract patterns and insights.
            """
            if not self.memory_system:
                raise HTTPException(status_code=503, detail="Memory system not initialized")

            try:
                await self.memory_system.consolidate_memories(mqtt_client=self._mqtt_client)
                return {"status": "consolidation_complete"}

            except Exception as e:
                self.logger.error(f"Error consolidating memories: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.delete("/working")
        async def clear_working_memory():
            """
            Clear working memory.

            Removes all interactions from working memory (volatile tier).
            """
            if not self.memory_system:
                raise HTTPException(status_code=503, detail="Memory system not initialized")

            try:
                await self.memory_system.clear_working_memory(mqtt_client=self._mqtt_client)
                return {"status": "working_memory_cleared"}

            except Exception as e:
                self.logger.error(f"Error clearing working memory: {e}")
                raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    # Run service
    service = MemoryService()
    asyncio.run(service.run())
