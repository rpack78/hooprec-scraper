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

- **Landing page** — Latest games grid with YouTube thumbnails, view counts; scores and winner highlights toggle with Watch/Stats mode
- **Watch/Stats mode** — Toggle in the header. Watch mode (default) hides scores and winners to avoid spoilers. Stats mode shows all outcome data.
- **Streaming chat** — Tokens appear in real-time as the LLM generates them
- **Source cards** — After each response, source citations display as cards with thumbnails, relevance scores, snippets, and YouTube links
- **Game discovery** — Quick prompt buttons ("Most exciting games", "Greatest comeback", etc.)
- **Top comments** — Click to load top YouTube comments for any game (via htmx)
- **Mode switching** — Toggle between Auto/Vector/SQL routing from the header
- **"Ask about this game"** — Click any game card to pre-fill a question about that matchup
- **Embedded video player** — Click any thumbnail to watch the video in an overlay modal; auto-marks as watched
- **Watch tracking** — Persistent watch history with green badges showing when you watched each video
- **Google OAuth** — Sign in to reply to YouTube comments directly from RecHoop
- **Data refresh** — One-click pipeline that re-scrapes hooprec.com, fetches new YouTube data, and re-ingests into ChromaDB with live SSE progress
- **Add Video** — Paste YouTube URLs on the Add Video page to check if they're in the database, process new videos, and submit match data (see below)
- **Suggested prompt caching** — When `PRELOAD_SUGGESTIONS=true`, suggested prompt responses are preloaded on startup and cached to disk (`data/db/preload_cache.json`). Cache auto-invalidates when game count changes.
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
| `SKIP_OLLAMA` | `false` | Disable Ollama transcript cleaning when adding videos via web |
| `PRELOAD_SUGGESTIONS` | `false` | Preload suggested prompt responses on startup (cached to disk) |

All other settings (LLM model, embeddings, TOP_K, etc.) are shared with the CLI — see [RAG Engine configuration](rag-engine.md#configuration).

## Add Video (Phase 4.1)

The Add Video page (`/add`, linked from the header) lets you manually add videos that the hooprec.com scraper may have missed.

1. **Paste URLs** — Enter one or more YouTube URLs into the textarea
2. **Check** — Videos already in the database show with a green "Already in database ✓" badge and match info
3. **Process** — Unknown videos are fully processed: metadata, transcript (Ollama cleanup unless `SKIP_OLLAMA=true`), and comments fetched via SSE streaming
4. **Review** — Pre-filled forms appear one at a time with guessed player names, scores, and date (regex on title first, Ollama LLM fallback). Non-1v1 videos are flagged with a warning.
5. **Submit** — Confirmed data creates match + player records, updates wins/losses, writes markdown, and auto-ingests into ChromaDB

### API Routes

| Route | Method | Description |
|---|---|---|
| `/add` | GET | Add Video page |
| `/api/add/check` | POST | Check which video IDs exist in DB |
| `/api/add/process` | POST | Process unknown videos (SSE stream) |
| `/api/add/submit` | POST | Submit user-corrected match data |

## Project Structure

```
rag/web/
├── app.py             # FastAPI app, SSE streaming, session mgmt, add video routes
├── db.py              # Direct SQLite queries (games, comments, watch history, add video)
├── templates/
│   ├── base.html      # Shell: Tailwind + htmx CDNs, dark theme
│   ├── index.html     # Landing page + chat (two-state layout)
│   ├── discover.html  # Add Video page (Phase 4.1)
│   └── partials/      # game_cards, source_cards, comments
└── static/
    ├── app.js         # SSE streaming, chat UI, source rendering, watch tracking
    └── discover.js    # Add Video: URL checking, SSE processing, review forms
```
