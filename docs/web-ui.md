# Web UI

> Back to [README](../README.md) · See also: [Architecture](architecture.md) · [RAG Engine](rag-engine.md)

Browser-based chat interface with game discovery, built on top of the [RAG Engine](rag-engine.md). All local, no build step.

## Stack

| Component | Choice | Why |
|---|---|---|
| **Web framework** | FastAPI | Async, lightweight, SSE support |
| **Templating** | Jinja2 | Server-rendered, built into FastAPI |
| **Styling** | Tailwind CSS (CDN) | No build step, rapid prototyping |
| **Dynamic updates** | htmx | Partial page updates without SPA complexity |
| **Streaming** | Server-Sent Events | Tokens stream in real-time like ChatGPT |

## Features

- **Landing page** — Latest games grid with YouTube thumbnails, scores, "winner" highlighting, view counts
- **Streaming chat** — Tokens appear in real-time as the LLM generates them
- **Source cards** — After each response, source citations display as cards with thumbnails, relevance scores, snippets, and YouTube links
- **Game discovery** — Quick prompt buttons ("Most exciting games", "Greatest comeback", etc.)
- **Top comments** — Click to load top YouTube comments for any game (via htmx)
- **Mode switching** — Toggle between Auto/Vector/SQL routing from the header
- **"Ask about this game"** — Click any game card to pre-fill a question about that matchup
- **Dark theme** — Basketball aesthetic with orange/amber accents

## Running It

```bash
# From project root (not rag/ directory)
pip install -r rag/requirements.txt
python -m rag.web.app
```

Open http://localhost:8000 in your browser.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `RAG_WEB_PORT` | `8000` | Web server port |

All other settings (LLM model, embeddings, TOP_K, etc.) are shared with the CLI — see [RAG Engine configuration](rag-engine.md#configuration).

## Project Structure

```
rag/web/
├── app.py             # FastAPI app, SSE streaming, session mgmt
├── db.py              # Direct SQLite queries (latest games, comments)
├── templates/
│   ├── base.html      # Shell: Tailwind + htmx CDNs, dark theme
│   ├── index.html     # Landing page + chat (two-state layout)
│   └── partials/      # game_cards, source_cards, comments
└── static/
    └── app.js         # SSE streaming, chat UI, source rendering
```
