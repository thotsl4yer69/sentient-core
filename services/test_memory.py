#!/usr/bin/env python3
"""
Memory System Tests
Comprehensive test suite for memory functionality.
"""

import asyncio
import pytest
import time
from memory import MemorySystem, Interaction, Memory


class TestMemorySystem:
    """Test suite for MemorySystem."""

    @pytest.fixture
    async def memory(self):
        """Create memory system instance."""
        mem = MemorySystem()
        await mem.connect()

        # Clear any existing data
        await mem.clear_working_memory()

        yield mem

        await mem.disconnect()

    @pytest.mark.asyncio
    async def test_store_interaction(self, memory):
        """Test storing an interaction."""
        interaction = await memory.store_interaction(
            "Hello, how are you?",
            "I'm doing well, thank you!"
        )

        assert interaction.user_msg == "Hello, how are you?"
        assert interaction.assistant_msg == "I'm doing well, thank you!"
        assert interaction.interaction_id is not None
        assert interaction.importance_score >= 0
        assert interaction.importance_score <= 1

    @pytest.mark.asyncio
    async def test_working_memory_fifo(self, memory):
        """Test FIFO behavior of working memory."""
        # Store more than max size
        for i in range(25):
            await memory.store_interaction(
                f"Message {i}",
                f"Response {i}"
            )

        # Should only keep last 20
        context = await memory.get_working_context()
        assert len(context) <= memory.WORKING_MAX_SIZE

        # Most recent should be first
        assert "Message 24" in context[0].user_msg

    @pytest.mark.asyncio
    async def test_importance_scoring(self, memory):
        """Test importance calculation."""
        # Low importance (short, generic)
        low_interaction = await memory.store_interaction(
            "ok",
            "sure"
        )
        assert low_interaction.importance_score < 0.3

        # High importance (personal, emotional)
        high_interaction = await memory.store_interaction(
            "I feel really happy about this! I love working on AI projects with you. "
            "I've decided to make this my main focus going forward.",
            "That's wonderful, Jack! I'm excited to help you with your AI journey. "
            "Your passion for this is clear."
        )
        assert high_interaction.importance_score > 0.5

    @pytest.mark.asyncio
    async def test_episodic_storage(self, memory):
        """Test episodic memory storage."""
        # Store important interaction
        await memory.store_interaction(
            "I really prefer working in Python for machine learning projects",
            "I'll remember that, Jack!",
            force_episodic=True
        )

        # Wait briefly for async storage
        await asyncio.sleep(0.1)

        # Should be searchable
        results = await memory.search_memories("Python programming", limit=5)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_semantic_search(self, memory):
        """Test semantic search functionality."""
        # Store interactions with semantic similarity
        await memory.store_interaction(
            "I love working with neural networks",
            "Neural networks are fascinating!",
            force_episodic=True
        )

        await memory.store_interaction(
            "Deep learning is amazing",
            "Yes, deep learning has revolutionized AI!",
            force_episodic=True
        )

        await memory.store_interaction(
            "What's the weather?",
            "I don't have weather data",
            force_episodic=True
        )

        # Wait for storage
        await asyncio.sleep(0.1)

        # Search for AI-related content
        results = await memory.search_memories(
            "artificial intelligence and machine learning",
            limit=10,
            min_similarity=0.3
        )

        # Should find AI-related, not weather
        assert len(results) >= 2
        for memory_obj, score in results:
            text = memory_obj.interaction.user_msg + memory_obj.interaction.assistant_msg
            if "neural" in text.lower() or "deep learning" in text.lower():
                # AI-related content should have reasonable similarity
                assert score > 0.3

    @pytest.mark.asyncio
    async def test_tag_extraction(self, memory):
        """Test automatic tag extraction."""
        # Work-related
        await memory.store_interaction(
            "Let's work on this project together",
            "Great! What's the task?",
            force_episodic=True
        )

        # Emotional
        await memory.store_interaction(
            "I feel excited about this!",
            "Your enthusiasm is great!",
            force_episodic=True
        )

        # Wait for storage
        await asyncio.sleep(0.1)

        # Check tags via search
        work_results = await memory.search_memories("project", limit=5)
        emotion_results = await memory.search_memories("feelings", limit=5)

        assert len(work_results) > 0
        assert len(emotion_results) > 0

    @pytest.mark.asyncio
    async def test_core_memory(self, memory):
        """Test core memory operations."""
        # Set values
        await memory.update_core_memory("name", "Jack")
        await memory.update_core_memory("age", 30)
        await memory.update_core_memory("preferences.color", "blue")
        await memory.update_core_memory("interests", ["AI", "robotics"])

        # Get individual values
        name = await memory.get_core_memory("name")
        assert name == "Jack"

        age = await memory.get_core_memory("age")
        assert age == 30

        color = await memory.get_core_memory("preferences.color")
        assert color == "blue"

        interests = await memory.get_core_memory("interests")
        assert "AI" in interests

        # Get all
        all_core = await memory.get_core_memory()
        assert "name" in all_core
        assert all_core["name"] == "Jack"

        # Delete
        await memory.delete_core_memory("age")
        age_after = await memory.get_core_memory("age")
        assert age_after is None

    @pytest.mark.asyncio
    async def test_time_range_filter(self, memory):
        """Test time range filtering in search."""
        now = time.time()

        # Store old interaction
        old_time = now - 3600  # 1 hour ago
        interaction_old = Interaction(
            user_msg="Old message",
            assistant_msg="Old response",
            timestamp=old_time,
            importance_score=0.8
        )
        await memory._store_episodic(interaction_old)

        # Store recent interaction
        interaction_recent = await memory.store_interaction(
            "Recent message",
            "Recent response",
            force_episodic=True
        )

        await asyncio.sleep(0.1)

        # Search with time range (last 30 minutes)
        recent_cutoff = now - 1800
        results = await memory.search_memories(
            "message",
            time_range=(recent_cutoff, now + 100),
            limit=10
        )

        # Should only find recent
        for memory_obj, score in results:
            assert memory_obj.interaction.timestamp >= recent_cutoff

    @pytest.mark.asyncio
    async def test_memory_stats(self, memory):
        """Test statistics reporting."""
        # Add some data
        await memory.store_interaction("msg1", "resp1")
        await memory.store_interaction("msg2", "resp2", force_episodic=True)
        await memory.update_core_memory("key1", "value1")

        stats = await memory.get_memory_stats()

        assert "working_memory" in stats
        assert "episodic_memory" in stats
        assert "core_memory" in stats
        assert stats["working_memory"]["count"] > 0

    @pytest.mark.asyncio
    async def test_consolidation(self, memory):
        """Test memory consolidation."""
        # Store multiple interactions
        for i in range(10):
            await memory.store_interaction(
                f"Work message {i}",
                f"Work response {i}",
                force_episodic=True
            )

        await asyncio.sleep(0.2)

        # Run consolidation (should not crash)
        await memory.consolidate_memories()

    @pytest.mark.asyncio
    async def test_export(self, memory, tmp_path):
        """Test memory export."""
        # Store some data
        await memory.store_interaction(
            "Export test message",
            "Export test response",
            force_episodic=True
        )

        await asyncio.sleep(0.1)

        # Export
        output_file = tmp_path / "export.json"
        await memory.export_episodic_memories(str(output_file))

        # Verify file exists and contains data
        assert output_file.exists()
        import json
        with open(output_file) as f:
            data = json.load(f)
        assert len(data) > 0

    @pytest.mark.asyncio
    async def test_clear_working(self, memory):
        """Test clearing working memory."""
        # Add data
        await memory.store_interaction("msg", "resp")

        # Clear
        await memory.clear_working_memory()

        # Should be empty
        context = await memory.get_working_context()
        assert len(context) == 0


# Manual test runner (if pytest not available)
async def run_manual_tests():
    """Run tests manually without pytest."""
    print("Running Memory System Tests...")
    print("="*60)

    memory = MemorySystem()
    await memory.connect()

    try:
        # Test 1: Store interaction
        print("\n1. Testing store_interaction...")
        interaction = await memory.store_interaction(
            "Hello, how are you?",
            "I'm doing well, thank you!"
        )
        assert interaction.interaction_id is not None
        print("✓ Store interaction works")

        # Test 2: Working memory
        print("\n2. Testing working memory...")
        context = await memory.get_working_context()
        assert len(context) > 0
        print(f"✓ Working memory contains {len(context)} interactions")

        # Test 3: Core memory
        print("\n3. Testing core memory...")
        await memory.update_core_memory("test_key", "test_value")
        value = await memory.get_core_memory("test_key")
        assert value == "test_value"
        print("✓ Core memory works")

        # Test 4: Search
        print("\n4. Testing semantic search...")
        await memory.store_interaction(
            "I love Python programming",
            "Python is great!",
            force_episodic=True
        )
        await asyncio.sleep(0.2)
        results = await memory.search_memories("Python coding", limit=5)
        print(f"✓ Search found {len(results)} results")

        # Test 5: Stats
        print("\n5. Testing statistics...")
        stats = await memory.get_memory_stats()
        assert "working_memory" in stats
        print("✓ Statistics work")

        print("\n" + "="*60)
        print("All manual tests passed!")

    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        raise

    finally:
        await memory.disconnect()


if __name__ == "__main__":
    # Try pytest first, fall back to manual
    try:
        import pytest
        pytest.main([__file__, "-v"])
    except ImportError:
        print("pytest not available, running manual tests...")
        asyncio.run(run_manual_tests())
