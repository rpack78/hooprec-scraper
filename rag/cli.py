"""
cli.py — Interactive REPL for the hooprec RAG system.

Commands:
    /quit     — Exit the REPL
    /sources  — Toggle source citation display
    /sql      — Force next query through the SQL engine
    /vector   — Force next query through the vector engine
    /auto     — Return to automatic routing (default)
    /clear    — Clear conversation history

Usage:
    python -m rag.cli
"""

from __future__ import annotations

import logging
import sys

from llama_index.core.chat_engine import CondenseQuestionChatEngine

from rag.query_engine import (
    build_router_query_engine,
    build_vector_query_engine,
    get_llm,
    get_sql_query_engine,
    get_vector_query_engine,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rag-cli")

BANNER = """
╔══════════════════════════════════════════════════╗
║          🏀  HoopRec RAG Chat  🏀               ║
║  Ask questions about 1v1 basketball matches      ║
║                                                  ║
║  Commands:                                       ║
║    /quit     Exit                                ║
║    /sources  Toggle source citations             ║
║    /sql      Force SQL path                      ║
║    /vector   Force vector path                   ║
║    /auto     Automatic routing (default)         ║
║    /clear    Clear conversation history          ║
╚══════════════════════════════════════════════════╝
"""


def _format_sources(response) -> str:
    """Extract source citations from a response."""
    if not hasattr(response, "source_nodes") or not response.source_nodes:
        return ""

    lines = ["\n--- Sources ---"]
    seen = set()
    for i, node in enumerate(response.source_nodes, 1):
        meta = node.metadata or {}
        source_file = meta.get("source_file", "unknown")

        # Deduplicate by source file
        if source_file in seen:
            continue
        seen.add(source_file)

        parts = [f"[{i}] {source_file}"]
        if meta.get("player1") and meta.get("player2"):
            parts.append(f"    Match: {meta['player1']} vs {meta['player2']}")
        if meta.get("youtube_url"):
            parts.append(f"    YouTube: {meta['youtube_url']}")
        if meta.get("section"):
            parts.append(f"    Section: {meta['section']}")
        score = getattr(node, "score", None)
        if score is not None:
            parts.append(f"    Relevance: {score:.3f}")

        # Short snippet
        text = node.get_content()[:200].replace("\n", " ")
        parts.append(f"    Snippet: {text}...")

        lines.append("\n".join(parts))

    return "\n".join(lines)


def main() -> None:
    print(BANNER)
    print("Loading engines... (this may take a moment on first run)")

    llm = get_llm()

    # Pre-build engines
    router_engine = build_router_query_engine()
    vector_index = build_vector_query_engine()
    vector_engine = get_vector_query_engine(vector_index)
    sql_engine = get_sql_query_engine()

    # Wrap the router in a chat engine for conversation context
    chat_engine = CondenseQuestionChatEngine.from_defaults(
        query_engine=router_engine,
        llm=llm,
    )

    show_sources = True
    force_mode: str | None = None  # None = auto, "sql", "vector"

    print("\nReady! Type your question or a command.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        # Handle commands
        if user_input.lower() == "/quit":
            print("Goodbye!")
            break
        elif user_input.lower() == "/sources":
            show_sources = not show_sources
            print(f"Source citations: {'ON' if show_sources else 'OFF'}")
            continue
        elif user_input.lower() == "/sql":
            force_mode = "sql"
            print("Mode: SQL (next queries forced through SQL engine)")
            continue
        elif user_input.lower() == "/vector":
            force_mode = "vector"
            print("Mode: Vector (next queries forced through vector engine)")
            continue
        elif user_input.lower() == "/auto":
            force_mode = None
            print("Mode: Auto (router decides)")
            continue
        elif user_input.lower() == "/clear":
            chat_engine.reset()
            print("Conversation history cleared.")
            continue

        # Query
        try:
            if force_mode == "sql":
                response = sql_engine.query(user_input)
            elif force_mode == "vector":
                response = vector_engine.query(user_input)
            else:
                response = chat_engine.chat(user_input)

            print(f"\nAssistant: {response}\n")

            if show_sources:
                sources = _format_sources(response)
                if sources:
                    print(sources)
                    print()

        except Exception as exc:
            print(f"\nError: {exc}\n")
            log.exception("Query failed")


if __name__ == "__main__":
    main()
