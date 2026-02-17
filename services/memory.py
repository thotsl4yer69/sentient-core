#!/usr/bin/env python3
"""
Sentient Core - Memory System
Three-tier memory architecture with Redis backend and semantic search.

Architecture:
- WORKING: Current conversation (last 20 exchanges, TTL 1 hour)
- EPISODIC: Significant interactions (searchable by embedding, persistent)
- CORE: Facts about Jack (manually curated, persistent)

Dependencies:
- redis: Redis client
- sentence-transformers: Semantic embeddings
- paho-mqtt: MQTT event publishing
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any
import hashlib
import os

import redis.asyncio as redis
import numpy as np
from sentence_transformers import SentenceTransformer
import paho.mqtt.client as mqtt

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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

    def __init__(
        self,
        redis_host: str = "localhost",
        redis_port: int = 6379,
        redis_db: int = 0,
        mqtt_broker: str = "localhost",
        mqtt_port: int = 1883,
        embedding_model: str = "all-MiniLM-L6-v2"
    ):
        """Initialize memory system."""
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_db = redis_db
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port

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

        # MQTT client
        self.mqtt_client = mqtt.Client()
        self.mqtt_connected = False

        # Sentence transformer for embeddings
        logger.info(f"Loading embedding model: {embedding_model}")
        self.encoder = SentenceTransformer(embedding_model)
        self.embedding_dim = self.encoder.get_sentence_embedding_dimension()

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

        # Connect to MQTT
        try:
            self.mqtt_client.on_connect = self._on_mqtt_connect
            self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
            self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port, 60)
            self.mqtt_client.loop_start()
            logger.info(f"Connected to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}")
        except Exception as e:
            logger.warning(f"Failed to connect to MQTT: {e}")
            # Don't raise - MQTT is optional

    async def disconnect(self):
        """Disconnect from Redis and MQTT."""
        if self.redis_client:
            await self.redis_client.close()
            logger.info("Disconnected from Redis")

        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            logger.info("Disconnected from MQTT")

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        """MQTT connection callback."""
        if rc == 0:
            self.mqtt_connected = True
            logger.info("MQTT connected successfully")
        else:
            logger.error(f"MQTT connection failed with code {rc}")

    def _on_mqtt_disconnect(self, client, userdata, rc):
        """MQTT disconnection callback."""
        self.mqtt_connected = False
        logger.warning(f"MQTT disconnected with code {rc}")

    def _publish_event(self, event_type: str, data: Dict[str, Any]):
        """Publish memory event to MQTT."""
        if not self.mqtt_connected:
            return

        try:
            payload = json.dumps({
                "type": event_type,
                "timestamp": time.time(),
                "data": data
            })
            self.mqtt_client.publish("sentient/memory/event", payload, qos=1)
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

    def _encode_text(self, text: str) -> List[float]:
        """Generate embedding for text."""
        embedding = self.encoder.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    async def store_interaction(
        self,
        user_msg: str,
        assistant_msg: str,
        force_episodic: bool = False
    ) -> Interaction:
        """
        Store interaction in working memory and optionally episodic.

        Args:
            user_msg: User's message
            assistant_msg: Assistant's response
            force_episodic: Force storage in episodic memory

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
            await self._store_episodic(interaction)

        # Publish event
        self._publish_event("interaction_stored", {
            "interaction_id": interaction.interaction_id,
            "importance": interaction.importance_score,
            "stored_episodic": force_episodic or interaction.importance_score >= self.EPISODIC_MIN_IMPORTANCE
        })

        return interaction

    async def _store_episodic(self, interaction: Interaction):
        """Store interaction in episodic memory with embedding."""
        # Generate embedding from combined text
        combined_text = f"{interaction.user_msg}\n{interaction.assistant_msg}"
        embedding = self._encode_text(combined_text)

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

        self._publish_event("episodic_stored", {
            "interaction_id": interaction.interaction_id,
            "tags": tags
        })

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
        time_range: Optional[Tuple[float, float]] = None
    ) -> List[Tuple[Memory, float]]:
        """
        Semantic search through episodic memories.

        Args:
            query: Search query
            limit: Maximum results
            min_similarity: Minimum cosine similarity threshold
            tags: Filter by tags (optional)
            time_range: (start_timestamp, end_timestamp) filter (optional)

        Returns:
            List of (Memory, similarity_score) tuples, sorted by similarity
        """
        # Generate query embedding
        query_embedding = np.array(self._encode_text(query))

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

        self._publish_event("memory_search", {
            "query": query,
            "results_count": len(results),
            "top_similarity": results[0][1] if results else 0.0
        })

        return results

    async def update_core_memory(self, key: str, value: Any):
        """
        Update core memory (facts about Jack).

        Args:
            key: Memory key (e.g., "name", "preferences.music")
            value: Memory value (can be any JSON-serializable type)
        """
        # Store in Redis hash
        await self.redis_client.hset(
            self.CORE_KEY,
            key,
            json.dumps(value)
        )

        logger.info(f"Updated core memory: {key} = {value}")

        self._publish_event("core_updated", {
            "key": key,
            "value": value
        })

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

    async def delete_core_memory(self, key: str):
        """Delete a core memory entry."""
        await self.redis_client.hdel(self.CORE_KEY, key)
        logger.info(f"Deleted core memory: {key}")

        self._publish_event("core_deleted", {
            "key": key
        })

    async def consolidate_memories(self):
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

        self._publish_event("memory_consolidation", {
            "total_memories": len(memory_ids),
            "tag_groups": {tag: len(interactions) for tag, interactions in tag_groups.items()}
        })

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

        return stats

    async def clear_working_memory(self):
        """Clear working memory."""
        await self.redis_client.delete(self.WORKING_KEY)
        logger.info("Cleared working memory")

        self._publish_event("working_cleared", {})

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


async def main():
    """Example usage and testing."""
    # Initialize memory system
    memory = MemorySystem(
        redis_host=os.getenv("REDIS_HOST", "localhost"),
        redis_port=int(os.getenv("REDIS_PORT", "6379")),
        mqtt_broker=os.getenv("MQTT_BROKER", "localhost"),
        mqtt_port=int(os.getenv("MQTT_PORT", "1883"))
    )

    try:
        # Connect
        await memory.connect()

        # Example 1: Store interactions
        print("\n=== Storing Interactions ===")
        await memory.store_interaction(
            "Hey Cortana, how are you today?",
            "I'm doing well, Jack! Ready to help you with anything you need."
        )

        await memory.store_interaction(
            "Remember that I prefer working on AI projects in the evenings.",
            "Noted, Jack. I'll remember that you prefer evening work sessions for AI projects.",
            force_episodic=True
        )

        await memory.store_interaction(
            "What's the weather like?",
            "I don't have real-time weather data, but I can help you find that information."
        )

        # Example 2: Get working context
        print("\n=== Working Context ===")
        context = await memory.get_working_context(limit=5)
        for i, interaction in enumerate(context):
            print(f"{i+1}. User: {interaction.user_msg[:50]}...")
            print(f"   Assistant: {interaction.assistant_msg[:50]}...")
            print(f"   Importance: {interaction.importance_score:.2f}")

        # Example 3: Search memories
        print("\n=== Searching Memories ===")
        results = await memory.search_memories("preferences and work habits", limit=3)
        for memory_obj, similarity in results:
            print(f"Similarity: {similarity:.3f}")
            print(f"User: {memory_obj.interaction.user_msg[:60]}...")
            print(f"Tags: {', '.join(memory_obj.tags)}")
            print()

        # Example 4: Core memory
        print("\n=== Core Memory ===")
        await memory.update_core_memory("name", "Jack")
        await memory.update_core_memory("preferences.work_time", "evenings")
        await memory.update_core_memory("preferences.topics", ["AI", "robotics", "automation"])

        core = await memory.get_core_memory()
        print("Core memories:")
        print(json.dumps(core, indent=2))

        # Example 5: Stats
        print("\n=== Memory Stats ===")
        stats = await memory.get_memory_stats()
        print(json.dumps(stats, indent=2))

        # Example 6: Memory consolidation
        print("\n=== Memory Consolidation ===")
        await memory.consolidate_memories()

    finally:
        # Cleanup
        await memory.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
