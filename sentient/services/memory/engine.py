#!/usr/bin/env python3
"""
Sentient Core - Memory System Engine
Three-tier memory architecture with Redis backend and semantic search.

Architecture:
- WORKING: Current conversation (last 20 exchanges, TTL 1 hour)
- EPISODIC: Significant interactions (searchable by embedding, persistent)
- CORE: Facts about Jack (manually curated, persistent)

Dependencies:
- redis: Redis client
- sentence-transformers: Semantic embeddings
- aiomqtt: Async MQTT event publishing
"""

import asyncio
import json
import time
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple, Any
import hashlib

import redis.asyncio as redis
import numpy as np
from sentence_transformers import SentenceTransformer
from aiomqtt import Client as MQTTClient

from sentient.config import get_config
from sentient.common.logging import setup_logging
from sentient.common import mqtt_topics

logger = setup_logging("memory")


@dataclass
class Interaction:
    """Single conversation interaction."""
    user_msg: str
    assistant_msg: str
    timestamp: float
    importance_score: float = 0.0
    interaction_id: Optional[str] = None

    def __post_init__(self):
        if self.interaction_id is None:
            # Generate unique ID from content and timestamp
            content = f"{self.user_msg}:{self.assistant_msg}:{self.timestamp}"
            self.interaction_id = hashlib.sha256(content.encode()).hexdigest()[:16]


@dataclass
class Memory:
    """Episodic memory entry with embedding."""
    interaction: Interaction
    embedding: Optional[List[float]] = None
    tags: List[str] = None
    context: Dict[str, Any] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.context is None:
            self.context = {}


class MemorySystem:
    """
    Three-tier memory system with Redis backend.

    Tiers:
    1. WORKING: Recent conversation context (volatile, TTL)
    2. EPISODIC: Searchable interaction history (persistent)
    3. CORE: Curated facts about Jack (persistent)
    """

    def __init__(self, embedding_model: Optional[str] = None):
        """Initialize memory system."""
        cfg = get_config()

        # Load config
        self.redis_host = cfg.redis.host
        self.redis_port = cfg.redis.port
        self.redis_db = cfg.redis.db
        self.mqtt_broker = cfg.mqtt.broker
        self.mqtt_port = cfg.mqtt.port
        self.mqtt_username = cfg.mqtt.username
        self.mqtt_password = cfg.mqtt.password

        # Redis keys
        self.WORKING_KEY = "memory:working"
        self.EPISODIC_KEY = "memory:episodic"
        self.EPISODIC_INDEX_KEY = "memory:episodic:index"
        self.CORE_KEY = "memory:core"

        # Configuration
        self.WORKING_MAX_SIZE = 20
        self.WORKING_TTL = 3600  # 1 hour
        self.EPISODIC_MIN_IMPORTANCE = 0.5

        # Redis client (initialized in connect)
        self.redis_client: Optional[redis.Redis] = None

        # MQTT client (initialized in connect)
        self._mqtt_client: Optional[MQTTClient] = None
        self.mqtt_connected = False

        # Sentence transformer for embeddings
        model_name = embedding_model or cfg.memory.embedding_model
        logger.info(f"Loading embedding model: {model_name}")
        self.encoder = SentenceTransformer(model_name)
        self.embedding_dim = self.encoder.get_sentence_embedding_dimension()

        # In-memory embedding cache for fast search
        self._embedding_matrix = None  # np.ndarray shape (N, embedding_dim)
        self._memory_ids = []  # List of memory ID strings, parallel to matrix rows
        self._memory_metadata = {}  # Dict[memory_id -> {interaction_data, tags_data, stored_at}]
        self._cache_loaded = False
        self._cache_lock = asyncio.Lock()
        self._pending_embeddings = []  # List of (embedding_array, memory_id, metadata) tuples
        self._pending_flush_size = 10  # Flush to matrix when this many pending

        logger.info("MemorySystem initialized")

    async def connect(self):
        """Connect to Redis and MQTT."""
        # Connect to Redis
        try:
            self.redis_client = await redis.Redis(
                host=self.redis_host,
                port=self.redis_port,
                db=self.redis_db,
                decode_responses=False  # We handle encoding ourselves
            )
            await self.redis_client.ping()
            logger.info(f"Connected to Redis at {self.redis_host}:{self.redis_port}")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

        # MQTT connection handled externally (by service wrapper)

        # Load embedding cache for fast search
        await self._load_embedding_cache()

        logger.info("MemorySystem connected")

    async def _load_embedding_cache(self):
        """Load all episodic embeddings into memory for vectorized search.

        Loads ~28K embeddings (384-dim float32) into a numpy matrix (~44MB).
        Uses Redis pipeline for batch reads instead of sequential HGETALL.
        """
        async with self._cache_lock:
            try:
                # Get all memory IDs from index
                all_ids = await self.redis_client.zrange(self.EPISODIC_INDEX_KEY, 0, -1)
                if not all_ids:
                    self._embedding_matrix = np.zeros((0, self.embedding_dim), dtype=np.float32)
                    self._memory_ids = []
                    self._memory_metadata = {}
                    self._cache_loaded = True
                    logger.info("Embedding cache loaded: 0 memories")
                    return

                # Batch read using pipeline
                embeddings = []
                valid_ids = []
                metadata = {}

                # Process in batches of 500 to avoid huge pipeline responses
                batch_size = 500
                id_list = [mid.decode('utf-8') if isinstance(mid, bytes) else mid for mid in all_ids]

                for batch_start in range(0, len(id_list), batch_size):
                    batch_ids = id_list[batch_start:batch_start + batch_size]
                    pipe = self.redis_client.pipeline(transaction=False)

                    for memory_id in batch_ids:
                        memory_key = f"{self.EPISODIC_KEY}:{memory_id}"
                        pipe.hgetall(memory_key)

                    results = await pipe.execute()

                    for memory_id, memory_data in zip(batch_ids, results):
                        if not memory_data:
                            continue
                        try:
                            embedding_data = json.loads(memory_data[b"embedding"].decode('utf-8'))
                            interaction_data = json.loads(memory_data[b"interaction"].decode('utf-8'))
                            tags_data = json.loads(memory_data[b"tags"].decode('utf-8'))
                            stored_at = float(memory_data.get(b"stored_at", b"0").decode('utf-8'))

                            embeddings.append(embedding_data)
                            valid_ids.append(memory_id)
                            metadata[memory_id] = {
                                'interaction': interaction_data,
                                'tags': tags_data,
                                'stored_at': stored_at
                            }
                        except Exception as e:
                            logger.warning(f"Skipping memory {memory_id}: {e}")
                            continue

                if embeddings:
                    self._embedding_matrix = np.array(embeddings, dtype=np.float32)
                    # Pre-normalize for cosine similarity (avoid per-query normalization)
                    norms = np.linalg.norm(self._embedding_matrix, axis=1, keepdims=True)
                    norms[norms == 0] = 1  # Avoid division by zero
                    self._embedding_matrix = self._embedding_matrix / norms
                else:
                    self._embedding_matrix = np.zeros((0, self.embedding_dim), dtype=np.float32)

                self._memory_ids = valid_ids
                self._memory_metadata = metadata
                self._cache_loaded = True

                cache_mb = self._embedding_matrix.nbytes / (1024 * 1024)
                logger.info(
                    f"Embedding cache loaded: {len(valid_ids)} memories, "
                    f"{cache_mb:.1f}MB matrix ({self._embedding_matrix.shape})"
                )

            except Exception as e:
                logger.error(f"Failed to load embedding cache: {e}", exc_info=True)
                self._cache_loaded = False

    async def disconnect(self):
        """Disconnect from Redis."""
        if self.redis_client:
            await self.redis_client.close()
            logger.info("Disconnected from Redis")

    async def _publish_event(self, event_type: str, data: Dict[str, Any], mqtt_client: Optional[MQTTClient] = None):
        """Publish memory event to MQTT."""
        if mqtt_client is None:
            return

        try:
            payload = json.dumps({
                "type": event_type,
                "timestamp": time.time(),
                "data": data
            })
            await mqtt_client.publish(mqtt_topics.MEMORY_EVENT, payload.encode())
        except Exception as e:
            logger.error(f"Failed to publish MQTT event: {e}")

    def _calculate_importance(self, user_msg: str, assistant_msg: str) -> float:
        """
        Calculate importance score for an interaction.

        Heuristics:
        - Length: Longer messages often indicate deeper engagement
        - Keywords: Personal info, decisions, emotions
        - Questions: User asking questions shows engagement
        - Follow-ups: Assistant asking questions shows relationship building
        """
        score = 0.0
        combined = f"{user_msg} {assistant_msg}".lower()

        # Length factor (normalized)
        length_score = min(len(combined) / 500, 1.0) * 0.2
        score += length_score

        # Personal keywords
        personal_keywords = [
            "i feel", "i think", "i want", "i need", "i love", "i hate",
            "my name", "i'm", "remember", "important", "prefer", "like",
            "jack", "cortana", "relationship", "together"
        ]
        personal_count = sum(1 for kw in personal_keywords if kw in combined)
        score += min(personal_count * 0.15, 0.4)

        # Emotional content
        emotion_keywords = [
            "happy", "sad", "angry", "excited", "worried", "scared",
            "love", "hate", "frustrated", "anxious", "grateful"
        ]
        emotion_count = sum(1 for kw in emotion_keywords if kw in combined)
        score += min(emotion_count * 0.1, 0.3)

        # Decision/commitment language
        decision_keywords = [
            "will", "going to", "planning", "decided", "promise",
            "commit", "definitely", "always", "never"
        ]
        decision_count = sum(1 for kw in decision_keywords if kw in combined)
        score += min(decision_count * 0.1, 0.2)

        # Questions (engagement)
        question_count = user_msg.count("?") + assistant_msg.count("?")
        score += min(question_count * 0.1, 0.2)

        # Normalize to [0, 1]
        return min(score, 1.0)

    def _extract_tags(self, user_msg: str, assistant_msg: str) -> List[str]:
        """Extract semantic tags from interaction."""
        tags = []
        combined = f"{user_msg} {assistant_msg}".lower()

        # Topic tags
        if any(kw in combined for kw in ["project", "work", "task", "build"]):
            tags.append("work")
        if any(kw in combined for kw in ["feel", "emotion", "happy", "sad"]):
            tags.append("emotional")
        if any(kw in combined for kw in ["remember", "recall", "memory"]):
            tags.append("meta-memory")
        if any(kw in combined for kw in ["jack", "you", "your"]):
            tags.append("about-jack")
        if any(kw in combined for kw in ["cortana", "i", "my", "me"]):
            tags.append("about-cortana")
        if "?" in user_msg:
            tags.append("question")
        if any(kw in combined for kw in ["plan", "future", "will", "going to"]):
            tags.append("planning")

        return tags

    async def _encode_text(self, text: str) -> List[float]:
        """Generate embedding for text (runs in executor to avoid blocking event loop)."""
        loop = asyncio.get_event_loop()
        embedding = await loop.run_in_executor(
            None, lambda: self.encoder.encode(text, convert_to_numpy=True)
        )
        return embedding.tolist()

    async def store_interaction(
        self,
        user_msg: str,
        assistant_msg: str,
        force_episodic: bool = False,
        mqtt_client: Optional[MQTTClient] = None
    ) -> Interaction:
        """
        Store interaction in working memory and optionally episodic.

        Args:
            user_msg: User's message
            assistant_msg: Assistant's response
            force_episodic: Force storage in episodic memory
            mqtt_client: Optional MQTT client for event publishing

        Returns:
            Interaction object
        """
        # Create interaction
        interaction = Interaction(
            user_msg=user_msg,
            assistant_msg=assistant_msg,
            timestamp=time.time()
        )

        # Calculate importance
        interaction.importance_score = self._calculate_importance(user_msg, assistant_msg)

        # Store in working memory (LPUSH + LTRIM)
        interaction_json = json.dumps(asdict(interaction))
        await self.redis_client.lpush(self.WORKING_KEY, interaction_json)
        await self.redis_client.ltrim(self.WORKING_KEY, 0, self.WORKING_MAX_SIZE - 1)
        await self.redis_client.expire(self.WORKING_KEY, self.WORKING_TTL)

        logger.info(
            f"Stored interaction in working memory "
            f"(importance: {interaction.importance_score:.2f})"
        )

        # Store in episodic if important enough
        if force_episodic or interaction.importance_score >= self.EPISODIC_MIN_IMPORTANCE:
            await self._store_episodic(interaction, mqtt_client)

        # Publish event
        await self._publish_event("interaction_stored", {
            "interaction_id": interaction.interaction_id,
            "importance": interaction.importance_score,
            "stored_episodic": force_episodic or interaction.importance_score >= self.EPISODIC_MIN_IMPORTANCE
        }, mqtt_client)

        return interaction

    async def _store_episodic(self, interaction: Interaction, mqtt_client: Optional[MQTTClient] = None):
        """Store interaction in episodic memory with embedding."""
        # Generate embedding from combined text
        combined_text = f"{interaction.user_msg}\n{interaction.assistant_msg}"
        embedding = await self._encode_text(combined_text)

        # Extract tags
        tags = self._extract_tags(interaction.user_msg, interaction.assistant_msg)

        # Create memory
        memory = Memory(
            interaction=interaction,
            embedding=embedding,
            tags=tags
        )

        # Store in Redis
        # Hash key: episodic:{interaction_id}
        memory_key = f"{self.EPISODIC_KEY}:{interaction.interaction_id}"
        memory_data = {
            "interaction": json.dumps(asdict(interaction)),
            "embedding": json.dumps(embedding),
            "tags": json.dumps(tags),
            "stored_at": time.time()
        }

        await self.redis_client.hset(memory_key, mapping=memory_data)

        # Add to index (sorted set by timestamp)
        await self.redis_client.zadd(
            self.EPISODIC_INDEX_KEY,
            {interaction.interaction_id: interaction.timestamp}
        )

        logger.info(
            f"Stored in episodic memory: {interaction.interaction_id} "
            f"(tags: {', '.join(tags)})"
        )

        await self._publish_event("episodic_stored", {
            "interaction_id": interaction.interaction_id,
            "tags": tags
        }, mqtt_client)

        # Append to pending buffer (avoids O(n) vstack per store)
        if self._cache_loaded and self._embedding_matrix is not None:
            try:
                new_emb = np.array(embedding, dtype=np.float32).reshape(1, -1)
                norm = np.linalg.norm(new_emb)
                if norm > 0:
                    new_emb = new_emb / norm
                self._pending_embeddings.append((new_emb, interaction.interaction_id, {
                    'interaction': asdict(interaction),
                    'tags': tags,
                    'stored_at': time.time()
                }))
                logger.debug(f"Pending buffer: {len(self._pending_embeddings)} items")

                # Flush when buffer is full
                if len(self._pending_embeddings) >= self._pending_flush_size:
                    await self._flush_pending_embeddings()
            except Exception as e:
                logger.warning(f"Failed to update embedding cache: {e}")

    async def _flush_pending_embeddings(self):
        """Flush pending embeddings into the main matrix (single vstack)."""
        if not self._pending_embeddings:
            return

        try:
            new_arrays = [emb for emb, _, _ in self._pending_embeddings]
            new_matrix = np.vstack(new_arrays)

            if self._embedding_matrix is not None and len(self._embedding_matrix) > 0:
                self._embedding_matrix = np.vstack([self._embedding_matrix, new_matrix])
            else:
                self._embedding_matrix = new_matrix

            for _, memory_id, metadata in self._pending_embeddings:
                self._memory_ids.append(memory_id)
                self._memory_metadata[memory_id] = metadata

            logger.info(f"Flushed {len(self._pending_embeddings)} embeddings to matrix (total: {len(self._memory_ids)})")
            self._pending_embeddings = []

        except Exception as e:
            logger.error(f"Failed to flush pending embeddings: {e}")

    async def get_working_context(self, limit: int = 20) -> List[Interaction]:
        """
        Get working memory context (recent interactions).

        Args:
            limit: Maximum number of interactions to return

        Returns:
            List of recent interactions (newest first)
        """
        # Get from Redis list
        interactions_json = await self.redis_client.lrange(self.WORKING_KEY, 0, limit - 1)

        interactions = []
        for interaction_json in interactions_json:
            try:
                data = json.loads(interaction_json)
                interaction = Interaction(**data)
                interactions.append(interaction)
            except Exception as e:
                logger.error(f"Failed to parse interaction: {e}")

        logger.info(f"Retrieved {len(interactions)} interactions from working memory")
        return interactions

    async def search_memories(
        self,
        query: str,
        limit: int = 5,
        min_similarity: float = 0.5,
        tags: Optional[List[str]] = None,
        time_range: Optional[Tuple[float, float]] = None,
        mqtt_client: Optional[MQTTClient] = None
    ) -> List[Tuple[Memory, float]]:
        """
        Semantic search through episodic memories using vectorized cosine similarity.

        Uses pre-loaded embedding matrix for O(1) similarity computation
        instead of sequential Redis reads. ~44MB memory for ~28K embeddings.
        """
        # Fall back to sequential if cache not loaded
        if not self._cache_loaded or self._embedding_matrix is None or len(self._memory_ids) == 0:
            logger.warning("Embedding cache not loaded, falling back to sequential search")
            return await self._search_memories_sequential(
                query, limit, min_similarity, tags, time_range, mqtt_client
            )

        # Flush any pending embeddings before searching
        if self._pending_embeddings:
            await self._flush_pending_embeddings()

        # Generate and normalize query embedding
        query_embedding = np.array(await self._encode_text(query), dtype=np.float32)
        query_norm = np.linalg.norm(query_embedding)
        if query_norm == 0:
            return []
        query_embedding = query_embedding / query_norm

        # Vectorized cosine similarity (matrix is pre-normalized)
        similarities = self._embedding_matrix @ query_embedding  # shape: (N,)

        # Build mask for filters
        mask = similarities >= min_similarity

        # Apply tag filter
        if tags:
            tag_mask = np.array([
                any(t in self._memory_metadata[mid].get('tags', []) for t in tags)
                for mid in self._memory_ids
            ], dtype=bool)
            mask &= tag_mask

        # Apply time range filter
        if time_range:
            start_ts, end_ts = time_range
            time_mask = np.array([
                start_ts <= self._memory_metadata[mid].get('stored_at', 0) <= end_ts
                for mid in self._memory_ids
            ], dtype=bool)
            mask &= time_mask

        # Get top results
        masked_sims = np.where(mask, similarities, -1.0)
        top_indices = np.argsort(masked_sims)[::-1][:limit]

        results = []
        for idx in top_indices:
            if masked_sims[idx] < min_similarity:
                break

            memory_id = self._memory_ids[idx]
            meta = self._memory_metadata[memory_id]

            interaction = Interaction(**meta['interaction'])
            memory = Memory(
                interaction=interaction,
                embedding=None,  # Don't return full embedding, saves memory
                tags=meta.get('tags', [])
            )
            results.append((memory, float(similarities[idx])))

        logger.info(
            f"Search '{query[:50]}' returned {len(results)} results "
            f"(cache: {len(self._memory_ids)} memories, threshold: {min_similarity})"
        )

        await self._publish_event("memory_search", {
            "query": query,
            "results_count": len(results),
            "top_similarity": results[0][1] if results else 0.0
        }, mqtt_client)

        return results

    async def _search_memories_sequential(
        self,
        query: str,
        limit: int = 5,
        min_similarity: float = 0.5,
        tags: Optional[List[str]] = None,
        time_range: Optional[Tuple[float, float]] = None,
        mqtt_client: Optional[MQTTClient] = None
    ) -> List[Tuple[Memory, float]]:
        """
        Sequential fallback: semantic search through episodic memories via Redis.

        Used when the in-memory embedding cache is not available.
        """
        # Generate query embedding
        query_embedding = np.array(await self._encode_text(query))

        # Get all episodic memory IDs
        if time_range:
            start_ts, end_ts = time_range
            memory_ids = await self.redis_client.zrangebyscore(
                self.EPISODIC_INDEX_KEY,
                start_ts,
                end_ts
            )
        else:
            memory_ids = await self.redis_client.zrange(self.EPISODIC_INDEX_KEY, 0, -1)

        # Retrieve and score memories
        results = []
        for memory_id in memory_ids:
            try:
                memory_id_str = memory_id.decode('utf-8') if isinstance(memory_id, bytes) else memory_id
                memory_key = f"{self.EPISODIC_KEY}:{memory_id_str}"
                memory_data = await self.redis_client.hgetall(memory_key)

                if not memory_data:
                    continue

                # Parse memory
                interaction_data = json.loads(memory_data[b"interaction"].decode('utf-8'))
                embedding_data = json.loads(memory_data[b"embedding"].decode('utf-8'))
                tags_data = json.loads(memory_data[b"tags"].decode('utf-8'))

                interaction = Interaction(**interaction_data)

                # Filter by tags if specified
                if tags and not any(tag in tags_data for tag in tags):
                    continue

                # Calculate cosine similarity
                memory_embedding = np.array(embedding_data)
                similarity = np.dot(query_embedding, memory_embedding) / (
                    np.linalg.norm(query_embedding) * np.linalg.norm(memory_embedding)
                )

                # Filter by similarity threshold
                if similarity >= min_similarity:
                    memory = Memory(
                        interaction=interaction,
                        embedding=embedding_data,
                        tags=tags_data
                    )
                    results.append((memory, float(similarity)))

            except Exception as e:
                logger.error(f"Error processing memory {memory_id}: {e}")
                continue

        # Sort by similarity (descending)
        results.sort(key=lambda x: x[1], reverse=True)
        results = results[:limit]

        logger.info(
            f"Search query '{query}' returned {len(results)} results "
            f"(threshold: {min_similarity})"
        )

        await self._publish_event("memory_search", {
            "query": query,
            "results_count": len(results),
            "top_similarity": results[0][1] if results else 0.0
        }, mqtt_client)

        return results

    async def update_core_memory(self, key: str, value: Any, mqtt_client: Optional[MQTTClient] = None):
        """
        Update core memory (facts about Jack).

        Args:
            key: Memory key (e.g., "name", "preferences.music")
            value: Memory value (can be any JSON-serializable type)
            mqtt_client: Optional MQTT client for event publishing
        """
        # Store in Redis hash
        await self.redis_client.hset(
            self.CORE_KEY,
            key,
            json.dumps(value)
        )

        logger.info(f"Updated core memory: {key} = {value}")

        await self._publish_event("core_updated", {
            "key": key,
            "value": value
        }, mqtt_client)

    async def get_core_memory(self, key: Optional[str] = None) -> Any:
        """
        Get core memory.

        Args:
            key: Specific key to retrieve, or None for all core memories

        Returns:
            Memory value or dict of all core memories
        """
        if key:
            value_json = await self.redis_client.hget(self.CORE_KEY, key)
            if value_json is None:
                return None
            return json.loads(value_json.decode('utf-8'))
        else:
            # Get all core memories
            all_data = await self.redis_client.hgetall(self.CORE_KEY)
            return {
                k.decode('utf-8'): json.loads(v.decode('utf-8'))
                for k, v in all_data.items()
            }

    async def delete_core_memory(self, key: str, mqtt_client: Optional[MQTTClient] = None):
        """Delete a core memory entry."""
        await self.redis_client.hdel(self.CORE_KEY, key)
        logger.info(f"Deleted core memory: {key}")

        await self._publish_event("core_deleted", {
            "key": key
        }, mqtt_client)

    async def consolidate_memories(self, mqtt_client: Optional[MQTTClient] = None):
        """
        Consolidate memories during idle periods.

        Process:
        1. Find patterns in recent episodic memories
        2. Extract high-level insights
        3. Update core memories if appropriate
        """
        logger.info("Starting memory consolidation")

        # Get recent episodic memories (last 24 hours)
        now = time.time()
        day_ago = now - 86400
        memory_ids = await self.redis_client.zrangebyscore(
            self.EPISODIC_INDEX_KEY,
            day_ago,
            now
        )

        if len(memory_ids) < 5:
            logger.info("Not enough recent memories to consolidate")
            return

        # Group by tags
        tag_groups: Dict[str, List[Interaction]] = {}
        for memory_id in memory_ids:
            try:
                memory_id_str = memory_id.decode('utf-8') if isinstance(memory_id, bytes) else memory_id
                memory_key = f"{self.EPISODIC_KEY}:{memory_id_str}"
                memory_data = await self.redis_client.hgetall(memory_key)

                if not memory_data:
                    continue

                interaction_data = json.loads(memory_data[b"interaction"].decode('utf-8'))
                tags_data = json.loads(memory_data[b"tags"].decode('utf-8'))

                interaction = Interaction(**interaction_data)

                for tag in tags_data:
                    if tag not in tag_groups:
                        tag_groups[tag] = []
                    tag_groups[tag].append(interaction)

            except Exception as e:
                logger.error(f"Error processing memory for consolidation: {e}")
                continue

        # Log consolidation summary
        logger.info(f"Consolidation summary: {len(memory_ids)} memories processed")
        for tag, interactions in tag_groups.items():
            logger.info(f"  {tag}: {len(interactions)} interactions")

        await self._publish_event("memory_consolidation", {
            "total_memories": len(memory_ids),
            "tag_groups": {tag: len(interactions) for tag, interactions in tag_groups.items()}
        }, mqtt_client)

    async def get_memory_stats(self) -> Dict[str, Any]:
        """Get memory system statistics."""
        working_count = await self.redis_client.llen(self.WORKING_KEY)
        episodic_count = await self.redis_client.zcard(self.EPISODIC_INDEX_KEY)
        core_count = await self.redis_client.hlen(self.CORE_KEY)

        stats = {
            "working_memory": {
                "count": working_count,
                "max_size": self.WORKING_MAX_SIZE,
                "ttl_seconds": self.WORKING_TTL
            },
            "episodic_memory": {
                "count": episodic_count,
                "min_importance_threshold": self.EPISODIC_MIN_IMPORTANCE
            },
            "core_memory": {
                "count": core_count
            },
            "embedding_model": self.encoder.get_sentence_embedding_dimension()
        }

        # Add cache stats
        if self._cache_loaded and self._embedding_matrix is not None:
            stats["embedding_cache"] = {
                "loaded": True,
                "entries": len(self._memory_ids),
                "matrix_shape": list(self._embedding_matrix.shape),
                "memory_mb": round(self._embedding_matrix.nbytes / (1024 * 1024), 1)
            }
        else:
            stats["embedding_cache"] = {"loaded": False}

        return stats

    async def clear_working_memory(self, mqtt_client: Optional[MQTTClient] = None):
        """Clear working memory."""
        await self.redis_client.delete(self.WORKING_KEY)
        logger.info("Cleared working memory")

        await self._publish_event("working_cleared", {}, mqtt_client)

    async def export_episodic_memories(
        self,
        output_path: str,
        time_range: Optional[Tuple[float, float]] = None
    ):
        """
        Export episodic memories to JSON file.

        Args:
            output_path: Path to output JSON file
            time_range: Optional (start_ts, end_ts) filter
        """
        # Get memory IDs
        if time_range:
            start_ts, end_ts = time_range
            memory_ids = await self.redis_client.zrangebyscore(
                self.EPISODIC_INDEX_KEY,
                start_ts,
                end_ts
            )
        else:
            memory_ids = await self.redis_client.zrange(self.EPISODIC_INDEX_KEY, 0, -1)

        # Retrieve all memories
        memories = []
        for memory_id in memory_ids:
            try:
                memory_id_str = memory_id.decode('utf-8') if isinstance(memory_id, bytes) else memory_id
                memory_key = f"{self.EPISODIC_KEY}:{memory_id_str}"
                memory_data = await self.redis_client.hgetall(memory_key)

                if not memory_data:
                    continue

                interaction_data = json.loads(memory_data[b"interaction"].decode('utf-8'))
                tags_data = json.loads(memory_data[b"tags"].decode('utf-8'))
                stored_at = float(memory_data[b"stored_at"].decode('utf-8'))

                memories.append({
                    "interaction": interaction_data,
                    "tags": tags_data,
                    "stored_at": stored_at
                })

            except Exception as e:
                logger.error(f"Error exporting memory: {e}")
                continue

        # Write to file
        with open(output_path, 'w') as f:
            json.dump(memories, f, indent=2)

        logger.info(f"Exported {len(memories)} episodic memories to {output_path}")
