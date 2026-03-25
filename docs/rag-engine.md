# RAG Engine

> Back to [README](../README.md) · See also: [Architecture](architecture.md) · [YouTube Ingest](youtube-ingest.md) · [Web UI](web-ui.md)

Hybrid RAG system using LlamaIndex + ChromaDB + Ollama. Fully local, no external APIs needed for inference.

## Stack

| Component | Choice | Why |
|---|---|---|
| **RAG framework** | LlamaIndex 0.14 | Purpose-built for RAG, first-class hybrid retrieval |
| **Vector store** | ChromaDB | Persistent, metadata filtering, zero infrastructure |
| **LLM** | Ollama (llama3.1:8b) | Free, private, already running for [YouTube Ingest](youtube-ingest.md) |
| **Embeddings** | nomic-embed-text via Ollama | Local, 768-dim embeddings, no API key needed |

## How It Works

1. **Ingest** — [YouTube Ingest](youtube-ingest.md) Markdown files (transcripts + comments + metadata) are chunked (512 tokens, 50 overlap) and embedded into ChromaDB via LlamaIndex with nomic-embed-text. Resumable — re-runs skip already-processed files.
2. **Retrieve** — User questions are routed by a `RouterQueryEngine`:
   - **Vector path** — Semantic search over transcripts and comments for narrative/opinion questions.
   - **SQL path** — `NLSQLTableQueryEngine` over `hooprec.sqlite` for stats/records queries.
   - **Common opponents** — `query_common_opponents()` wrapped as a custom query engine for player-vs-player comparison queries.
   - **Hybrid** — `SubQuestionQueryEngine` decomposes complex queries into sub-parts hitting both engines.
3. **Generate** — Retrieved context is passed to Ollama which synthesizes a grounded answer with citations and YouTube links.

## Example Queries

| Question | Data needed | Retrieval path |
|---|---|---|
| *"What's the most popular 1v1 involving Left Hand Dom?"* | `youtube_videos.view_count` + `matches` join | SQL |
| *"Show me a game with a controversial incident"* | Transcript text search + comment sentiment | Vector |
| *"Who has Qel beat that Skoob has lost to?"* | `query_common_opponents()` SQL helper | Common opponents |
| *"What do fans think of Nasir Core?"* | Comment text across all his match videos | Vector |
| *"Summarize Left Hand Dom vs Chris Lykes"* | Match stats + transcript + top comments | Hybrid (both) |

## Running It

```bash
# Install dependencies
cd rag
pip install -r requirements.txt

# Ensure Ollama models are available
ollama pull llama3.1:8b
ollama pull nomic-embed-text

# Ingest YouTube markdown into ChromaDB (run once, re-run for new matches)
cd ..
python -m rag.ingest
python -m rag.ingest --reset   # wipe and re-ingest everything

# Start the interactive CLI
python -m rag.cli
```

For the browser-based interface, see [Web UI](web-ui.md).

## CLI Commands

| Command | Description |
|---|---|
| `/quit` | Exit the REPL |
| `/sources` | Toggle source citation display |
| `/sql` | Force next queries through SQL engine |
| `/vector` | Force next queries through vector engine |
| `/auto` | Return to automatic routing (default) |
| `/clear` | Clear conversation history |

## Configuration

| Variable | Default | Description |
|---|---|---|
| `RAG_LLM_MODEL` | `llama3.1:8b` | Ollama LLM model for synthesis |
| `RAG_EMBED_MODEL` | `nomic-embed-text` | Ollama embedding model |
| `RAG_CHUNK_SIZE` | `512` | Token chunk size for transcript splitting |
| `RAG_CHUNK_OVERLAP` | `50` | Token overlap between chunks |
| `RAG_TOP_K` | `5` | Number of chunks to retrieve |
| `RAG_CONTEXT_WINDOW` | `8192` | LLM context window size |
| `RAG_LLM_TIMEOUT` | `120` | LLM request timeout (seconds) |
| `CHROMA_DIR` | `data/db/chroma` | ChromaDB persistent storage path |
| `SKIP_OLLAMA` | `false` | Disable Ollama transcript cleaning globally |
| `PRELOAD_SUGGESTIONS` | `false` | Preload suggested prompt responses on web server startup (persisted to disk) |

## Switching LLM Models

The default `llama3.1:8b` works but struggles with JSON routing and SQL generation. Upgrading to a larger model improves quality — all models are free and run locally via Ollama.

| Model | VRAM needed | Strengths | Notes |
|---|---|---|---|
| `llama3.1:8b` (default) | ~5 GB | Fast, good for transcripts | Weak at structured output (JSON, SQL) |
| **`qwen2.5:14b` (recommended)** | **~9 GB** | **Better reasoning, reliable JSON/SQL** | **Fits a 12 GB GPU (e.g. RTX 4070 Super)** |
| `gemma3:12b` | ~8 GB | Good instruction following | Also fits 12 GB |

To switch, pull the model and update your `.env`:

```bash
ollama pull qwen2.5:14b
```

```env
# .env
RAG_LLM_MODEL=qwen2.5:14b
```

No re-ingestion needed — only the LLM changes, the embedding model and ChromaDB stay the same.

## Tests

35 unit tests covering the ingestion pipeline and CLI formatting. No Ollama, ChromaDB, or live data needed — all tests use temporary markdown fixtures.

```bash
python -m pytest rag/tests/ -v
```

| Module | Tests | What's covered |
|---|---|---|
| `parse_youtube_md` | 16 | Player names, dates, URLs, views/likes as ints, transcript/comment splitting, edge cases |
| `_parse_int` | 3 | Comma-separated numbers, zero |
| `build_documents` | 9 | Doc counts, metadata propagation, excluded keys, empty dirs, non-yt files |
| `_filter_new_documents` | 3 | Resumability: skip ingested, keep new, partial filter |
| `_format_sources` | 4 | CLI citation formatting, deduplication, missing attributes |
