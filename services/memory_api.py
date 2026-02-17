#!/usr/bin/env python3
"""
Memory System API Wrapper
Simplified interface for interacting with the memory system.
"""

import asyncio
import json
from typing import List, Dict, Optional, Any
from memory import MemorySystem, Interaction, Memory


class MemoryAPI:
    """High-level API for memory operations."""

    def __init__(self, memory_system: Optional[MemorySystem] = None):
        """
        Initialize API wrapper.

        Args:
            memory_system: Existing MemorySystem instance, or None to create new
        """
        self.memory = memory_system or MemorySystem()
        self._connected = False

    async def __aenter__(self):
        """Async context manager entry."""
        if not self._connected:
            await self.memory.connect()
            self._connected = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._connected:
            await self.memory.disconnect()
            self._connected = False

    async def remember(self, user_msg: str, assistant_msg: str) -> str:
        """
        Store interaction and return interaction ID.

        Args:
            user_msg: User's message
            assistant_msg: Assistant's response

        Returns:
            Interaction ID
        """
        interaction = await self.memory.store_interaction(user_msg, assistant_msg)
        return interaction.interaction_id

    async def recall(
        self,
        query: str,
        limit: int = 5,
        format: str = "text"
    ) -> Any:
        """
        Search memories and return formatted results.

        Args:
            query: Search query
            limit: Maximum results
            format: "text", "json", or "objects"

        Returns:
            Formatted search results
        """
        results = await self.memory.search_memories(query, limit=limit)

        if format == "objects":
            return results

        if format == "json":
            return [
                {
                    "user_msg": mem.interaction.user_msg,
                    "assistant_msg": mem.interaction.assistant_msg,
                    "timestamp": mem.interaction.timestamp,
                    "tags": mem.tags,
                    "similarity": score
                }
                for mem, score in results
            ]

        # Text format
        output = []
        for i, (mem, score) in enumerate(results):
            output.append(f"\n--- Result {i+1} (similarity: {score:.3f}) ---")
            output.append(f"User: {mem.interaction.user_msg}")
            output.append(f"Assistant: {mem.interaction.assistant_msg}")
            output.append(f"Tags: {', '.join(mem.tags)}")

        return "\n".join(output)

    async def get_context(self, format: str = "text") -> Any:
        """
        Get recent conversation context.

        Args:
            format: "text", "json", or "objects"

        Returns:
            Formatted conversation context
        """
        interactions = await self.memory.get_working_context()

        if format == "objects":
            return interactions

        if format == "json":
            return [
                {
                    "user_msg": i.user_msg,
                    "assistant_msg": i.assistant_msg,
                    "timestamp": i.timestamp,
                    "importance": i.importance_score
                }
                for i in interactions
            ]

        # Text format
        output = []
        for i, interaction in enumerate(reversed(interactions)):
            output.append(f"\n[{i+1}]")
            output.append(f"User: {interaction.user_msg}")
            output.append(f"Assistant: {interaction.assistant_msg}")

        return "\n".join(output)

    async def know(self, key: str, value: Any = None) -> Any:
        """
        Get or set core memory fact.

        Args:
            key: Memory key
            value: Value to set (None to get)

        Returns:
            Current value (for get) or None (for set)
        """
        if value is None:
            return await self.memory.get_core_memory(key)
        else:
            await self.memory.update_core_memory(key, value)
            return None

    async def forget(self, key: str):
        """
        Delete core memory fact.

        Args:
            key: Memory key to delete
        """
        await self.memory.delete_core_memory(key)

    async def get_all_facts(self) -> Dict[str, Any]:
        """Get all core memory facts."""
        return await self.memory.get_core_memory()

    async def consolidate(self):
        """Run memory consolidation process."""
        await self.memory.consolidate_memories()

    async def stats(self) -> Dict[str, Any]:
        """Get memory system statistics."""
        return await self.memory.get_memory_stats()

    async def export(self, output_path: str):
        """
        Export episodic memories to JSON file.

        Args:
            output_path: Path to output file
        """
        await self.memory.export_episodic_memories(output_path)


async def example_usage():
    """Example API usage."""
    async with MemoryAPI() as api:
        # Store interactions
        print("Storing interactions...")
        await api.remember(
            "I really enjoy working on robotics projects.",
            "That's great! I'll remember your interest in robotics."
        )

        await api.remember(
            "My favorite programming language is Python.",
            "Noted! Python is excellent for many applications."
        )

        # Search memories
        print("\n\nSearching for 'programming'...")
        results = await api.recall("programming", format="text")
        print(results)

        # Get context
        print("\n\nRecent context:")
        context = await api.get_context(format="text")
        print(context)

        # Core memory
        print("\n\nCore facts:")
        await api.know("name", "Jack")
        await api.know("interests", ["robotics", "AI", "Python"])
        facts = await api.get_all_facts()
        print(json.dumps(facts, indent=2))

        # Stats
        print("\n\nSystem stats:")
        stats = await api.stats()
        print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    asyncio.run(example_usage())
