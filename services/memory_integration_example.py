#!/usr/bin/env python3
"""
Memory System Integration Example
Demonstrates integration with conversation and proactive systems.
"""

import asyncio
import json
from datetime import datetime
from memory_api import MemoryAPI


class ConversationWithMemory:
    """Example conversation system enhanced with memory."""

    def __init__(self):
        self.memory_api = None

    async def start(self):
        """Initialize with memory system."""
        self.memory_api = MemoryAPI()
        await self.memory_api.memory.connect()
        print("Conversation system started with memory")

    async def stop(self):
        """Cleanup."""
        if self.memory_api:
            await self.memory_api.memory.disconnect()
        print("Conversation system stopped")

    async def process_message(self, user_msg: str) -> str:
        """
        Process user message with memory context.

        Flow:
        1. Retrieve relevant memories
        2. Get recent conversation context
        3. Generate response considering context
        4. Store interaction
        """
        print(f"\n{'='*60}")
        print(f"User: {user_msg}")
        print(f"{'='*60}")

        # 1. Search for relevant memories
        print("\nSearching relevant memories...")
        relevant_memories = await self.memory_api.recall(
            user_msg,
            limit=3,
            format="objects"
        )

        if relevant_memories:
            print(f"Found {len(relevant_memories)} relevant memories:")
            for memory, score in relevant_memories:
                print(f"  - [Score: {score:.2f}] {memory.interaction.user_msg[:50]}...")
        else:
            print("No relevant memories found")

        # 2. Get recent context
        print("\nRetrieving conversation context...")
        recent_context = await self.memory_api.get_context(format="objects")
        print(f"Context: {len(recent_context)} recent interactions")

        # 3. Check core facts
        print("\nChecking core facts...")
        user_name = await self.memory_api.know("name")
        if user_name:
            print(f"User name: {user_name}")

        # 4. Generate response (simplified - would use LLM here)
        assistant_msg = self._generate_response(
            user_msg,
            relevant_memories,
            recent_context,
            user_name
        )

        # 5. Store interaction
        print("\nStoring interaction...")
        await self.memory_api.remember(user_msg, assistant_msg)

        print(f"\nAssistant: {assistant_msg}")
        print(f"{'='*60}\n")

        return assistant_msg

    def _generate_response(
        self,
        user_msg: str,
        relevant_memories,
        recent_context,
        user_name
    ) -> str:
        """
        Generate response based on context.
        In production, this would call an LLM with the context.
        """
        # Simple rule-based response for demo
        user_msg_lower = user_msg.lower()

        # Use name if known
        greeting = f"Hello {user_name}!" if user_name else "Hello!"

        # Check for memory-related queries
        if "remember" in user_msg_lower or "recall" in user_msg_lower:
            if relevant_memories:
                return f"{greeting} Yes, I remember we discussed that before. Let me share what I recall..."
            else:
                return f"{greeting} I don't have specific memories about that topic yet."

        # Check for preference-related queries
        if "prefer" in user_msg_lower or "like" in user_msg_lower:
            return f"{greeting} I'm learning your preferences. I'll remember this for future conversations."

        # Context-aware response
        if len(recent_context) > 5:
            return f"{greeting} Based on our ongoing conversation, I understand you're interested in this topic."

        # Default
        return f"{greeting} I'm here to help! I'll remember our conversation to better assist you in the future."


async def demo_conversation():
    """Demonstrate memory-enhanced conversation."""
    conv = ConversationWithMemory()
    await conv.start()

    try:
        # Simulate conversation with memory
        print("\n" + "="*60)
        print("MEMORY-ENHANCED CONVERSATION DEMO")
        print("="*60)

        # Set core facts
        await conv.memory_api.know("name", "Jack")
        await conv.memory_api.know("preferences.communication", "direct and efficient")

        # Conversation sequence
        await conv.process_message(
            "I prefer working on AI projects in the evenings"
        )

        await conv.process_message(
            "My favorite programming language is Python"
        )

        await conv.process_message(
            "I'm interested in robotics and automation"
        )

        await conv.process_message(
            "What do you remember about my work preferences?"
        )

        await conv.process_message(
            "Do you recall what programming language I like?"
        )

        # Show memory stats
        print("\n" + "="*60)
        print("MEMORY STATISTICS")
        print("="*60)
        stats = await conv.memory_api.stats()
        print(json.dumps(stats, indent=2))

        # Show all core facts
        print("\n" + "="*60)
        print("CORE FACTS")
        print("="*60)
        facts = await conv.memory_api.get_all_facts()
        print(json.dumps(facts, indent=2))

    finally:
        await conv.stop()


async def demo_proactive_memory():
    """Demonstrate using memory for proactive suggestions."""
    print("\n" + "="*60)
    print("PROACTIVE MEMORY DEMO")
    print("="*60)

    async with MemoryAPI() as api:
        # Simulate user establishing patterns
        print("\nEstablishing user patterns...")

        patterns = [
            ("It's 9 PM, time to work on my AI project", "Great! Evening is your productive time."),
            ("I always work on robotics after dinner", "I'll remember that pattern."),
            ("I prefer Python for machine learning tasks", "Noted! Python is great for ML."),
        ]

        for user_msg, assistant_msg in patterns:
            await api.remember(user_msg, assistant_msg)

        # Consolidate to identify patterns
        print("\nConsolidating memories...")
        await api.consolidate()

        # Search for evening work pattern
        print("\nSearching for evening work patterns...")
        results = await api.recall(
            "evening work programming",
            limit=5,
            format="text"
        )
        print(results)

        # Proactive suggestion based on time and memory
        current_hour = datetime.now().hour
        if 20 <= current_hour <= 23:  # 8 PM to 11 PM
            print("\n[PROACTIVE SUGGESTION]")
            print("It's evening - would you like to work on your AI/robotics project?")
            print("I remember this is your preferred time for technical work.")


async def demo_memory_search_filters():
    """Demonstrate advanced search with filters."""
    print("\n" + "="*60)
    print("ADVANCED SEARCH DEMO")
    print("="*60)

    async with MemoryAPI() as api:
        # Store diverse interactions
        interactions = [
            ("I'm feeling happy about the project progress", "That's wonderful!"),
            ("I'm worried about the deadline", "Let's plan to stay on track."),
            ("Tell me about neural networks", "Neural networks are..."),
            ("What's the weather like?", "I don't have weather data."),
        ]

        for user_msg, assistant_msg in interactions:
            await api.remember(user_msg, assistant_msg)

        # Search with different queries
        queries = [
            "emotions and feelings",
            "technical topics",
            "planning and projects"
        ]

        for query in queries:
            print(f"\nSearching: '{query}'")
            results = await api.recall(query, limit=2, format="objects")
            for memory, score in results:
                print(f"  [{score:.2f}] User: {memory.interaction.user_msg}")
                print(f"         Tags: {', '.join(memory.tags)}")


async def main():
    """Run all demos."""
    print("\n" + "="*80)
    print(" SENTIENT CORE - MEMORY SYSTEM INTEGRATION EXAMPLES")
    print("="*80)

    # Demo 1: Conversation with memory
    await demo_conversation()

    await asyncio.sleep(1)

    # Demo 2: Proactive memory
    await demo_proactive_memory()

    await asyncio.sleep(1)

    # Demo 3: Advanced search
    await demo_memory_search_filters()

    print("\n" + "="*80)
    print(" DEMOS COMPLETE")
    print("="*80)


if __name__ == "__main__":
    asyncio.run(main())
