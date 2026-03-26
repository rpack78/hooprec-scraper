# 1v1 Basketball RAG Scraper

A knowledge base and conversational AI project built on top of [hooprec.com](https://hooprec.com) — a 1v1 basketball stats site that tracks head-to-head matchups, player records, scores, and game film.

The goal: **ask natural-language questions about 1v1 basketball** and get answers grounded in real data.

> *"What's the most popular 1v1 involving Left Hand Dom?"*
> *"Show me a game with a controversial incident."*
> *"Who has Qel beat that Skoob has lost to — and where can I watch those games?"*

## Documentation

| Page | Description |
|---|---|
| [Architecture](docs/architecture.md) | System diagram, data sources, database stats, project structure |
| [HoopRec Scraper](docs/hooprec-scraper.md) | Match data ingestion from hooprec.com (daily scheduled) |
| [YouTube Ingest](docs/youtube-ingest.md) | Video metadata, transcripts, comments enrichment |
| [RAG Engine](docs/rag-engine.md) | LlamaIndex + ChromaDB hybrid retrieval, CLI, tests, model selection |
| [Web UI](docs/web-ui.md) | Browser-based chat with game discovery, watch tracking, video player, and YouTube commenting |

## Quick Start

```bash
# ── Install dependencies ──
pip install -r hooprec-ingest/requirements.txt
pip install -r youtube-ingest/requirements.txt
pip install -r rag/requirements.txt
playwright install chromium
ollama pull llama3.1:8b
ollama pull nomic-embed-text

# ── Run pipelines ──
python hooprec-ingest/hooprec_master_ingest.py   # scrape hooprec.com
python youtube-ingest/youtube_ingest.py          # enrich with YouTube data
python -m rag.ingest                             # embed into ChromaDB

# ── Launch ──
python -m rag.cli                 # interactive CLI
python -m rag.web                 # web UI at localhost:8000
```

## Applying Configuration Changes

If you change `.env` settings like `SKIP_OLLAMA=true` or `PRELOAD_SUGGESTIONS=true`, you need to reprocess the affected data:

```bash
# 1. Refresh YouTube markdown (Applies SKIP_OLLAMA)
python youtube-ingest/youtube_ingest.py

# 2. Re-embed vector data (Wipes old embeddings and re-ingests)
python -m rag.ingest --reset

# 3. Regenerate the Preload Cache (Applies PRELOAD_SUGGESTIONS on startup)
python -m rag.web
```
*(Note: To force the web suggestion cache to regenerate without a change in game count, manually delete `data/db/preload_cache.json` before starting the server).*

## Tech Stack

| Layer | Tools |
|---|---|
| **Scraping** | crawl4ai, Playwright, HoopRec REST API |
| **YouTube** | YouTube Data API v3, youtube-transcript-api, Ollama (punctuation agent) |
| **RAG** | LlamaIndex, ChromaDB, Ollama (llama3.1:8b / qwen2.5:14b), nomic-embed-text |
| **Web** | FastAPI, Jinja2, Tailwind CSS, htmx, SSE streaming |
| **Storage** | SQLite, ChromaDB, Markdown files |

## License

Private — not for redistribution.
