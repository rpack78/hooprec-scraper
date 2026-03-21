"""
config.py — Centralized configuration for the RAG pipeline.

All values can be overridden via environment variables or a .env file
in the project root.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = _SCRIPT_DIR.parent

load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DB_PATH = Path(os.getenv("HOOPREC_DB", str(PROJECT_ROOT / "data" / "db" / "hooprec.sqlite")))
YOUTUBE_MD_DIR = Path(os.getenv("YOUTUBE_MD_DIR", str(PROJECT_ROOT / "data" / "raw" / "youtube_md")))
CHROMA_DIR = Path(os.getenv("CHROMA_DIR", str(PROJECT_ROOT / "data" / "db" / "chroma")))
SCHEMA_FILE = PROJECT_ROOT / "hooprec-ingest" / "schema.sql"

# ---------------------------------------------------------------------------
# Ollama models
# ---------------------------------------------------------------------------
LLM_MODEL = os.getenv("RAG_LLM_MODEL", "llama3.1:8b")
EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "nomic-embed-text")

# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "512"))
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "50"))

# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
TOP_K = int(os.getenv("RAG_TOP_K", "5"))

# ---------------------------------------------------------------------------
# LLM parameters
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT = float(os.getenv("RAG_LLM_TIMEOUT", "120.0"))
CONTEXT_WINDOW = int(os.getenv("RAG_CONTEXT_WINDOW", "8192"))

# ---------------------------------------------------------------------------
# ChromaDB collection
# ---------------------------------------------------------------------------
CHROMA_COLLECTION = os.getenv("RAG_CHROMA_COLLECTION", "hooprec_youtube")
