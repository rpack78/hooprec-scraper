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
# ── HoopRec Scraper ──
cd hooprec-ingest
pip install -r requirements.txt
playwright install chromium
python hooprec_master_ingest.py

# ── YouTube Ingest ──
cd ../youtube-ingest
pip install -r requirements.txt
python youtube_ingest.py

# ── RAG Chat ──
cd ../rag
pip install -r requirements.txt
ollama pull llama3.1:8b
ollama pull nomic-embed-text

cd ..
python -m rag.ingest              # embed YouTube markdown into ChromaDB
python -m rag.cli                 # interactive CLI
python -m rag.web.app             # web UI at localhost:8000
                                  # Discover page at localhost:8000/discover
```

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
