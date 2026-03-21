"""
query_engine.py — Vector + SQL query engines with hybrid router.

Provides:
- Vector engine over ChromaDB (transcripts + comments)
- SQL engine over hooprec.sqlite
- query_common_opponents wrapped as a FunctionTool
- RouterQueryEngine for automatic routing
- SubQuestionQueryEngine for complex hybrid queries
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import chromadb
from llama_index.core import (
    SQLDatabase,
    Settings,
    VectorStoreIndex,
)
from llama_index.core.question_gen import LLMQuestionGenerator
from llama_index.core.query_engine import (
    NLSQLTableQueryEngine,
    RouterQueryEngine,
    SubQuestionQueryEngine,
)
from llama_index.core.selectors import LLMSingleSelector
from llama_index.core.tools import FunctionTool, QueryEngineTool, ToolMetadata
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.vector_stores.chroma import ChromaVectorStore
from sqlalchemy import create_engine

from rag.config import (
    CHROMA_COLLECTION,
    CHROMA_DIR,
    CONTEXT_WINDOW,
    DB_PATH,
    EMBED_MODEL,
    LLM_MODEL,
    REQUEST_TIMEOUT,
    TOP_K,
)

log = logging.getLogger("rag-engine")

# ---------------------------------------------------------------------------
# LLM + Embedding singletons
# ---------------------------------------------------------------------------

_llm: Ollama | None = None
_embed: OllamaEmbedding | None = None


def get_llm() -> Ollama:
    global _llm
    if _llm is None:
        _llm = Ollama(
            model=LLM_MODEL,
            request_timeout=REQUEST_TIMEOUT,
            context_window=CONTEXT_WINDOW,
        )
        Settings.llm = _llm
    return _llm


def get_embed_model() -> OllamaEmbedding:
    global _embed
    if _embed is None:
        _embed = OllamaEmbedding(model_name=EMBED_MODEL)
        Settings.embed_model = _embed
    return _embed


# ---------------------------------------------------------------------------
# Vector query engine
# ---------------------------------------------------------------------------


def build_vector_query_engine() -> VectorStoreIndex:
    """Load existing ChromaDB collection and return a VectorStoreIndex."""
    chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = chroma_client.get_or_create_collection(CHROMA_COLLECTION)

    vector_store = ChromaVectorStore(chroma_collection=collection)
    index = VectorStoreIndex.from_vector_store(
        vector_store=vector_store,
        embed_model=get_embed_model(),
    )
    return index


def get_vector_query_engine(index: VectorStoreIndex | None = None):
    """Return a query engine over the vector store."""
    if index is None:
        index = build_vector_query_engine()
    return index.as_query_engine(
        llm=get_llm(),
        similarity_top_k=TOP_K,
    )


# ---------------------------------------------------------------------------
# SQL query engine
# ---------------------------------------------------------------------------


def get_sql_query_engine() -> NLSQLTableQueryEngine:
    """Build NLSQLTableQueryEngine over hooprec.sqlite."""
    db_url = f"sqlite:///{DB_PATH}"
    engine = create_engine(db_url)
    sql_db = SQLDatabase(
        engine,
        include_tables=["players", "matches", "player_matches", "youtube_videos"],
    )
    return NLSQLTableQueryEngine(
        sql_database=sql_db,
        llm=get_llm(),
        tables=["players", "matches", "player_matches", "youtube_videos"],
    )


# ---------------------------------------------------------------------------
# query_common_opponents as FunctionTool
# ---------------------------------------------------------------------------


def _query_common_opponents(player_a: str, player_b: str) -> str:
    """Find opponents that player_a has beaten AND player_b has lost to.

    Returns a formatted string with opponent names and YouTube links for
    both the winning and losing games.

    Args:
        player_a: Name of the first player (the one who won).
        player_b: Name of the second player (the one who lost).
    """
    # Import the actual query function from Phase 1
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "hooprec-ingest"))
    from hooprec_master_ingest import query_common_opponents

    conn = sqlite3.connect(DB_PATH)
    try:
        results = query_common_opponents(conn, player_a, player_b)
    finally:
        conn.close()

    if not results:
        return f"No common opponents found where {player_a} won and {player_b} lost."

    lines = [f"Opponents that {player_a} beat and {player_b} lost to:\n"]
    for r in results:
        lines.append(
            f"- **{r['opponent']}**\n"
            f"  {player_a}'s game: {r['player_a_youtube'] or 'no video'}\n"
            f"  {player_b}'s game: {r['player_b_youtube'] or 'no video'}"
        )
    return "\n".join(lines)


common_opponents_tool = FunctionTool.from_defaults(
    fn=_query_common_opponents,
    name="query_common_opponents",
    description=(
        "Compare two players by finding opponents that player_a has beaten "
        "and player_b has lost to. Use this for questions like "
        "'Who has X beat that Y has lost to?' Returns opponent names with "
        "YouTube links for both games."
    ),
)


# ---------------------------------------------------------------------------
# Hybrid router
# ---------------------------------------------------------------------------


def build_router_query_engine():
    """Build a RouterQueryEngine that routes between vector, SQL, and
    the common-opponents function tool."""
    llm = get_llm()

    # Vector engine
    vector_index = build_vector_query_engine()
    vector_engine = vector_index.as_query_engine(
        llm=llm,
        similarity_top_k=TOP_K,
    )

    # SQL engine
    sql_engine = get_sql_query_engine()

    # Query engine tools for the router
    vector_tool = QueryEngineTool(
        query_engine=vector_engine,
        metadata=ToolMetadata(
            name="transcript_and_comments_search",
            description=(
                "Search through YouTube video transcripts and fan comments "
                "for 1v1 basketball matches. Use this for narrative questions, "
                "opinions, controversial moments, trash talk, game summaries, "
                "what fans think about players, and any question about what "
                "happened during a game. Also good for finding specific "
                "moments or quotes from games."
            ),
        ),
    )

    sql_tool = QueryEngineTool(
        query_engine=sql_engine,
        metadata=ToolMetadata(
            name="stats_and_records_database",
            description=(
                "Query the structured database of 1v1 basketball stats. "
                "Use this ONLY for purely factual/numerical questions: "
                "win/loss records, exact scores, view counts, match dates, "
                "which players have played each other, and counting stats. "
                "Do NOT use this for subjective questions like 'who is the "
                "best' or 'who is the greatest' — those need fan opinions "
                "too. Tables: players (name, wins, losses), matches "
                "(scores, winner, loser, youtube_url, match_date), "
                "player_matches (result, score), youtube_videos "
                "(view_count, like_count, title, channel)."
            ),
        ),
    )

    common_opp_tool = QueryEngineTool.from_defaults(
        query_engine=_build_common_opp_query_engine(llm),
        name="common_opponents_comparison",
        description=(
            "Compare two players by finding opponents that one has beaten "
            "and the other has lost to. Use for questions like "
            "'Who has X beat that Y lost to?' or player-vs-player comparison "
            "questions involving shared opponents."
        ),
    )

    # Sub-question engine for complex queries that need both sources
    question_gen = LLMQuestionGenerator.from_defaults(llm=llm)
    sub_question_engine = SubQuestionQueryEngine.from_defaults(
        query_engine_tools=[vector_tool, sql_tool, common_opp_tool],
        llm=llm,
        question_gen=question_gen,
    )

    sub_q_tool = QueryEngineTool(
        query_engine=sub_question_engine,
        metadata=ToolMetadata(
            name="hybrid_sub_question_engine",
            description=(
                "Use this for questions that are subjective or need BOTH "
                "stats AND fan opinions/comments. Examples: 'who is the "
                "best player', 'who is the GOAT', 'who is overrated', "
                "'who had a fall-off', summarizing a specific match, "
                "finding the most popular game with a controversial "
                "moment. Any question where stats alone don't fully "
                "answer it should use this. Decomposes complex queries "
                "into sub-questions hitting both stats and transcript/"
                "comment engines."
            ),
        ),
    )

    router = RouterQueryEngine(
        selector=LLMSingleSelector.from_defaults(llm=llm),
        query_engine_tools=[vector_tool, sql_tool, common_opp_tool, sub_q_tool],
    )

    return router


def _build_common_opp_query_engine(llm):
    """Wrap the common_opponents FunctionTool into a simple query engine."""
    from llama_index.core.query_engine import CustomQueryEngine

    class CommonOpponentsQueryEngine(CustomQueryEngine):
        """Custom query engine that extracts player names and runs
        query_common_opponents."""

        def custom_query(self, query_str: str) -> str:
            # Ask the LLM to extract the two player names
            extraction_prompt = (
                "Extract exactly two player names from this question. "
                "The first player is the one who WON against opponents, "
                "the second is the one who LOST to those same opponents. "
                "Reply with ONLY the two names separated by a pipe character |. "
                "Example: Qel|Skoob\n\n"
                f"Question: {query_str}"
            )
            resp = llm.complete(extraction_prompt)
            names = resp.text.strip().split("|")

            if len(names) != 2:
                return (
                    "I couldn't identify two player names in your question. "
                    "Please rephrase like: 'Who has [Player A] beat that [Player B] lost to?'"
                )

            player_a = names[0].strip()
            player_b = names[1].strip()
            return _query_common_opponents(player_a, player_b)

    return CommonOpponentsQueryEngine()
