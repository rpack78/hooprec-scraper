"""
ingest.py — Load YouTube markdown files, chunk, embed, and store in ChromaDB.

Resumeable: tracks ingested filenames so re-runs skip already-processed docs.

Usage:
    python -m rag.ingest              # ingest all new files
    python -m rag.ingest --reset      # wipe collection and re-ingest everything
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

import chromadb
from llama_index.core import Document, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore

from rag.config import (
    CHROMA_COLLECTION,
    CHROMA_DIR,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    EMBED_MODEL,
    YOUTUBE_MD_DIR,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rag-ingest")

# ---------------------------------------------------------------------------
# Metadata parsing
# ---------------------------------------------------------------------------

_META_PATTERNS: dict[str, re.Pattern] = {
    "player1": re.compile(r"^#\s+(.+?)\s+vs\s+(.+)", re.IGNORECASE),
    "match_date": re.compile(r"\*\*Match date:\*\*\s*(.+)", re.IGNORECASE),
    "youtube_url": re.compile(r"\*\*YouTube:\*\*\s*(https?://\S+)", re.IGNORECASE),
    "title": re.compile(r"-\s*\*\*Title:\*\*\s*(.+)", re.IGNORECASE),
    "channel": re.compile(r"-\s*\*\*Channel:\*\*\s*(.+)", re.IGNORECASE),
    "views": re.compile(r"-\s*\*\*Views:\*\*\s*([\d,]+)", re.IGNORECASE),
    "likes": re.compile(r"-\s*\*\*Likes:\*\*\s*([\d,]+)", re.IGNORECASE),
    "duration": re.compile(r"-\s*\*\*Duration:\*\*\s*(.+)", re.IGNORECASE),
}


def _parse_int(s: str) -> int:
    return int(s.replace(",", ""))


def parse_youtube_md(path: Path) -> dict:
    """Extract metadata + sections from a YouTube markdown file."""
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")

    meta: dict = {"source_file": path.name}

    for line in lines[:20]:
        # Player names from the H1 heading
        m = _META_PATTERNS["player1"].match(line)
        if m:
            meta["player1"] = m.group(1).strip()
            meta["player2"] = m.group(2).strip()
            continue

        for key in ("match_date", "youtube_url", "title", "channel", "duration"):
            m = _META_PATTERNS[key].search(line)
            if m:
                meta[key] = m.group(1).strip()

        for key in ("views", "likes"):
            m = _META_PATTERNS[key].search(line)
            if m:
                meta[key] = _parse_int(m.group(1))

    # Split into transcript and comments sections
    transcript_text = ""
    comments_text = ""

    # Find section boundaries
    transcript_start = None
    comments_start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("## Transcript"):
            transcript_start = i + 1
        elif line.strip().startswith("## Top Comments"):
            comments_start = i + 1

    if transcript_start is not None:
        end = comments_start - 1 if comments_start else len(lines)
        transcript_text = "\n".join(lines[transcript_start:end]).strip()

    if comments_start is not None:
        comments_text = "\n".join(lines[comments_start:]).strip()

    return {
        "metadata": meta,
        "transcript": transcript_text,
        "comments": comments_text,
    }


# ---------------------------------------------------------------------------
# Document creation
# ---------------------------------------------------------------------------


def build_documents(md_dir: Path) -> list[Document]:
    """Load all YouTube markdown files and return LlamaIndex Documents.

    Creates separate documents for transcript vs comments sections so they
    can be chunked independently (transcript gets split; comments stay whole).
    """
    docs: list[Document] = []
    md_files = sorted(md_dir.glob("yt_*.md"))

    if not md_files:
        log.warning("No YouTube markdown files found in %s", md_dir)
        return docs

    for path in md_files:
        parsed = parse_youtube_md(path)
        meta = parsed["metadata"]

        if parsed["transcript"]:
            docs.append(Document(
                text=parsed["transcript"],
                metadata={**meta, "section": "transcript"},
                excluded_llm_metadata_keys=["source_file", "section"],
                excluded_embed_metadata_keys=["source_file"],
            ))

        if parsed["comments"]:
            docs.append(Document(
                text=parsed["comments"],
                metadata={**meta, "section": "comments"},
                excluded_llm_metadata_keys=["source_file", "section"],
                excluded_embed_metadata_keys=["source_file"],
            ))

    log.info("Built %d documents from %d markdown files", len(docs), len(md_files))
    return docs


# ---------------------------------------------------------------------------
# Resumability helpers
# ---------------------------------------------------------------------------

_PROGRESS_META_KEY = "__ingested_files__"


def _get_ingested_files(collection: chromadb.Collection) -> set[str]:
    """Return set of source_file values already in the collection."""
    try:
        result = collection.get(include=["metadatas"])
        if result and result["metadatas"]:
            return {m["source_file"] for m in result["metadatas"] if "source_file" in m}
    except Exception:
        pass
    return set()


def _filter_new_documents(docs: list[Document], already_ingested: set[str]) -> list[Document]:
    """Keep only documents whose source_file isn't already in the collection."""
    new_docs = [d for d in docs if d.metadata.get("source_file") not in already_ingested]
    skipped = len(docs) - len(new_docs)
    if skipped:
        log.info("Skipping %d already-ingested documents", skipped)
    return new_docs


# ---------------------------------------------------------------------------
# Main ingest pipeline
# ---------------------------------------------------------------------------


def run_ingest(reset: bool = False) -> None:
    """Run the full ingestion pipeline."""
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)

    # Chroma client
    chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    if reset:
        log.info("Resetting collection '%s'", CHROMA_COLLECTION)
        try:
            chroma_client.delete_collection(CHROMA_COLLECTION)
        except ValueError:
            pass  # collection didn't exist

    collection = chroma_client.get_or_create_collection(CHROMA_COLLECTION)

    # Build documents
    all_docs = build_documents(YOUTUBE_MD_DIR)
    if not all_docs:
        log.error("No documents to ingest — aborting.")
        return

    # Filter to new-only (resumability)
    already = _get_ingested_files(collection)
    docs = _filter_new_documents(all_docs, already)
    if not docs:
        log.info("All documents already ingested. Nothing to do.")
        return

    log.info("Ingesting %d new documents ...", len(docs))

    # Embedding model
    embed_model = OllamaEmbedding(model_name=EMBED_MODEL)

    # Chunking — transcript docs get split, comments stay as single chunks
    splitter = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)

    transcript_docs = [d for d in docs if d.metadata.get("section") == "transcript"]
    comment_docs = [d for d in docs if d.metadata.get("section") == "comments"]

    # Split transcripts into nodes
    transcript_nodes = splitter.get_nodes_from_documents(transcript_docs) if transcript_docs else []
    # Comments: one node per document (no splitting)
    comment_nodes = splitter.get_nodes_from_documents(comment_docs) if comment_docs else []

    all_nodes = transcript_nodes + comment_nodes
    log.info(
        "Chunked into %d nodes (%d transcript, %d comment)",
        len(all_nodes), len(transcript_nodes), len(comment_nodes),
    )

    # Vector store + index
    vector_store = ChromaVectorStore(chroma_collection=collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    VectorStoreIndex(
        nodes=all_nodes,
        storage_context=storage_context,
        embed_model=embed_model,
        show_progress=True,
    )

    final_count = collection.count()
    log.info("Done. ChromaDB collection '%s' now has %d chunks.", CHROMA_COLLECTION, final_count)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest YouTube markdown into ChromaDB")
    parser.add_argument("--reset", action="store_true", help="Wipe collection and re-ingest")
    args = parser.parse_args()
    run_ingest(reset=args.reset)


if __name__ == "__main__":
    main()
