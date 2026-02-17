#!/usr/bin/env python3
"""
Memory System CLI
Command-line interface for memory operations.
"""

import asyncio
import argparse
import json
import sys
from memory_api import MemoryAPI


async def cmd_store(api: MemoryAPI, args):
    """Store an interaction."""
    interaction_id = await api.remember(args.user_msg, args.assistant_msg)
    print(f"Stored interaction: {interaction_id}")


async def cmd_search(api: MemoryAPI, args):
    """Search memories."""
    results = await api.recall(args.query, limit=args.limit, format=args.format)
    if args.format == "json":
        print(json.dumps(results, indent=2))
    else:
        print(results)


async def cmd_context(api: MemoryAPI, args):
    """Get conversation context."""
    context = await api.get_context(format=args.format)
    if args.format == "json":
        print(json.dumps(context, indent=2))
    else:
        print(context)


async def cmd_know(api: MemoryAPI, args):
    """Get or set core memory."""
    if args.value:
        # Set
        try:
            value = json.loads(args.value)
        except json.JSONDecodeError:
            value = args.value
        await api.know(args.key, value)
        print(f"Set {args.key} = {value}")
    else:
        # Get
        value = await api.know(args.key)
        if value is None:
            print(f"No value for key: {args.key}")
        else:
            print(json.dumps(value, indent=2))


async def cmd_forget(api: MemoryAPI, args):
    """Delete core memory."""
    await api.forget(args.key)
    print(f"Deleted: {args.key}")


async def cmd_facts(api: MemoryAPI, args):
    """Show all core facts."""
    facts = await api.get_all_facts()
    print(json.dumps(facts, indent=2))


async def cmd_stats(api: MemoryAPI, args):
    """Show memory statistics."""
    stats = await api.stats()
    print(json.dumps(stats, indent=2))


async def cmd_consolidate(api: MemoryAPI, args):
    """Run memory consolidation."""
    print("Running consolidation...")
    await api.consolidate()
    print("Consolidation complete")


async def cmd_export(api: MemoryAPI, args):
    """Export episodic memories."""
    print(f"Exporting to {args.output}...")
    await api.export(args.output)
    print("Export complete")


async def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Sentient Core Memory System CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Store interaction
  memory store "What's your name?" "I'm Cortana, your AI assistant."

  # Search memories
  memory search "preferences" --limit 5

  # Get conversation context
  memory context --format json

  # Set core fact
  memory know name '"Jack"'
  memory know interests '["robotics", "AI"]'

  # Get core fact
  memory know name

  # Show all facts
  memory facts

  # Statistics
  memory stats

  # Export memories
  memory export memories.json
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # store command
    store_parser = subparsers.add_parser("store", help="Store an interaction")
    store_parser.add_argument("user_msg", help="User's message")
    store_parser.add_argument("assistant_msg", help="Assistant's response")

    # search command
    search_parser = subparsers.add_parser("search", help="Search memories")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--limit", type=int, default=5, help="Max results")
    search_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format"
    )

    # context command
    context_parser = subparsers.add_parser("context", help="Get conversation context")
    context_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format"
    )

    # know command
    know_parser = subparsers.add_parser("know", help="Get or set core memory")
    know_parser.add_argument("key", help="Memory key")
    know_parser.add_argument("value", nargs="?", help="Value to set (JSON)")

    # forget command
    forget_parser = subparsers.add_parser("forget", help="Delete core memory")
    forget_parser.add_argument("key", help="Memory key to delete")

    # facts command
    subparsers.add_parser("facts", help="Show all core facts")

    # stats command
    subparsers.add_parser("stats", help="Show memory statistics")

    # consolidate command
    subparsers.add_parser("consolidate", help="Run memory consolidation")

    # export command
    export_parser = subparsers.add_parser("export", help="Export episodic memories")
    export_parser.add_argument("output", help="Output JSON file path")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Command dispatch
    commands = {
        "store": cmd_store,
        "search": cmd_search,
        "context": cmd_context,
        "know": cmd_know,
        "forget": cmd_forget,
        "facts": cmd_facts,
        "stats": cmd_stats,
        "consolidate": cmd_consolidate,
        "export": cmd_export
    }

    try:
        async with MemoryAPI() as api:
            await commands[args.command](api, args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
