---
description: "Phase 3.1 implementation plan — Web chat UI for RecHoop RAG system"
---

# Phase 3.1: Web Chat UI

## Plan: RecHoop Web Chat Interface

**TL;DR**: Build a web UI for the existing RAG chat system using FastAPI + Jinja2 + htmx + Tailwind CSS. Landing page shows latest ingested 1v1 games with thumbnails and top comments. Chat interface streams LLM responses token-by-token via SSE and displays source cards (with YouTube thumbnails/links) alongside answers. All Python, no build step, local-only.

---

## Architecture

- **Backend**: FastAPI serving Jinja2 templates + SSE streaming
- **Frontend**: htmx for dynamic updates, Tailwind CSS (CDN) for styling, ~50 lines vanilla JS for SSE chat streaming
- **No build step**: Tailwind via CDN (Play CDN), htmx via CDN
- **Data flow**: Reuses existing `rag/query_engine.py` engines + direct SQLAlchemy queries for structured data
- **Thumbnails**: Derived from YouTube video_id → `https://img.youtube.com/vi/{VIDEO_ID}/hqdefault.jpg`

---

## File Structure

```
rag/
  web/
    __init__.py
    app.py              # FastAPI app: routes, SSE streaming, session mgmt
    db.py               # Direct SQLite queries (latest games, comments)
    templates/
      base.html         # Shell: Tailwind CDN, htmx CDN, app.js
      index.html        # Main page: landing feed + chat interface
      partials/
        game_card.html  # Reusable game card (thumbnail, players, score, link)
        source_card.html # Source citation card (from RAG response)
        chat_message.html # Single chat message bubble (user or assistant)
        comments.html   # Top comments popover/section for a game
    static/
      app.js            # SSE streaming logic, chat scroll, minor interactivity
      favicon.ico       # Optional
```

---

## Steps

### Phase A — Backend API (depends on nothing)

**Step 1**: Create `rag/web/db.py` — direct SQLite helper functions
- `get_latest_games(limit=12)` — Query matches + youtube_videos tables, join to get title, view_count, channel_name, video_id. Order by match_date DESC. Return list of dicts with thumbnail_url derived from video_id.
- `get_top_comments(video_id, limit=5)` — Query youtube_comments table ordered by like_count DESC. Return list of dicts.
- `get_game_details(video_id)` — Full details for one game (match scores, comments, transcript availability).
- Use existing `rag.config.DB_PATH` for connection string.
- Relevant existing code: `hooprec-ingest/schema.sql` for table structure, `rag/config.py` for DB_PATH.

**Step 2**: Create `rag/web/app.py` — FastAPI application
- Mount static files and Jinja2 templates directories
- Session management: in-memory dict keyed by session cookie (UUID). Each session holds a `CondenseQuestionChatEngine` instance (reuse pattern from `rag/cli.py`).
- Engine initialization on first request (lazy — build once, reuse).

  **Routes:**
  - `GET /` — Render index.html with latest games from `db.get_latest_games()`
  - `GET /api/games/latest` — htmx partial: returns rendered game_card.html fragments
  - `GET /api/games/{video_id}/comments` — htmx partial: returns rendered comments.html
  - `POST /api/chat` — Accept `{"message": "..."}`, return SSE stream:
    - Use `chat_engine.stream_chat(message)` from LlamaIndex
    - Stream tokens as `event: token` SSE events
    - After stream completes, extract source_nodes, render source_card.html partials, send as `event: sources` SSE event
    - Send `event: done` to signal completion
  - `POST /api/chat/clear` — Reset session's chat engine history
  - `GET /api/chat/mode/{mode}` — Switch engine mode (auto/sql/vector), mirrors CLI's /sql, /vector, /auto commands

**Step 3**: Update `rag/requirements.txt` — add dependencies
- `fastapi>=0.115.0`
- `uvicorn[standard]>=0.34.0`
- `jinja2>=3.1.0`
- `sse-starlette>=2.0.0` (SSE helper for FastAPI)

### Phase B — Templates & Frontend (parallel with Step 2 after Step 1)

**Step 4**: Create `rag/web/templates/base.html` — page shell
- Tailwind CSS Play CDN in `<head>`
- htmx CDN + SSE extension in `<head>`
- `<script src="/static/app.js">` before closing body
- Dark theme (basketball/sports aesthetic — dark bg, orange/amber accents)
- Responsive layout container

**Step 5**: Create `rag/web/templates/index.html` — main page (extends base.html)
- **Layout**: Two-state layout
  - **Landing state** (before first chat): Full-width grid of latest game cards + centered search/chat input
  - **Chat state** (after first message): Left panel = chat thread (scrollable), Right panel = source cards that update per response
- **Chat input**: Fixed at bottom, form with htmx POST (but JS intercepts for SSE streaming)
- **Mode switcher**: Small toggle/dropdown for auto/sql/vector mode

**Step 6**: Create template partials
- `game_card.html` — YouTube thumbnail (derived from video_id), player1 vs player2, score (winner highlighted), match date, view count, channel name, "Watch on YouTube" link. Clicking shows comments via htmx GET.
- `source_card.html` — Similar to game_card but adds: relevance score badge, section type (transcript/comments), text snippet (first 150 chars). Used for RAG response sources.
- `chat_message.html` — Message bubble. User messages right-aligned (blue), assistant messages left-aligned (gray). Assistant messages have a "Sources" indicator.
- `comments.html` — List of top comments: author, text, like count. Loaded on-demand via htmx.

**Step 7**: Create `rag/web/static/app.js` — minimal client-side JS (~80 lines)
- `sendMessage(text)` — Opens EventSource to `/api/chat`, streams tokens into the current assistant message bubble, on `sources` event swaps source cards into the right panel, on `done` event re-enables input.
- Auto-scroll chat to bottom on new tokens.
- Extract video_id from youtube_url helper (for cases where only URL is in metadata).

### Phase C — Integration & Polish (depends on A + B)

**Step 8**: Wire streaming end-to-end
- Adapt `rag/query_engine.py` `build_router_query_engine()` — no changes needed, already returns the engine
- In `app.py`, wrap engine with `CondenseQuestionChatEngine` per session (same as cli.py does)
- Use `.stream_chat()` for streaming, iterate `response.response_gen` for tokens
- After stream, access `response.source_nodes` for source cards
- Extract `video_id` from `youtube_url` metadata field: `url.split("v=")[1].split("&")[0]`
- Derive thumbnail: `f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"`

**Step 9**: Add entry point
- Add `if __name__ == "__main__": uvicorn.run(...)` to app.py
- Add run command to README: `python -m rag.web.app`
- Default port: 8000 (configurable via env var `RAG_WEB_PORT`)

**Step 10**: Update README.md
- Add Phase 3.1 section with screenshots placeholder, run instructions, URL

---

## Relevant Files

### Reuse directly (no modifications)
- `rag/query_engine.py` — `build_router_query_engine()`, `build_vector_query_engine()`, `get_sql_query_engine()`, `get_llm()`, `get_embed_model()` — all engine construction
- `rag/config.py` — `DB_PATH`, `CHROMA_DIR`, `LLM_MODEL`, `EMBED_MODEL`, `TOP_K`, `CONTEXT_WINDOW`, `CHROMA_COLLECTION`
- `hooprec-ingest/schema.sql` — Reference for SQLite queries (matches, youtube_videos, youtube_comments table structures)

### Reference for patterns
- `rag/cli.py` — `_format_sources()` function for source node field extraction pattern; `main()` for CondenseQuestionChatEngine wrapping + fallback logic on router failure — replicate both patterns in web app
- `rag/ingest.py` — `parse_youtube_md()` for understanding metadata field names stored in ChromaDB

### New files
- `rag/web/__init__.py` — empty
- `rag/web/app.py` — FastAPI app
- `rag/web/db.py` — SQLite queries
- `rag/web/templates/base.html`, `index.html`, `partials/*.html` — templates
- `rag/web/static/app.js` — client JS

### Modify
- `rag/requirements.txt` — Add fastapi, uvicorn, jinja2, sse-starlette
- `README.md` — Add Phase 3.1 docs

---

## Verification

1. **Start server**: `python -m rag.web.app` → confirms FastAPI starts, serves index page at http://localhost:8000
2. **Landing page**: Open browser → latest games display with thumbnails, player names, scores, YouTube links
3. **Click game card**: Top comments load dynamically via htmx
4. **Chat basic**: Type "Who has the most wins?" → streaming response appears token by token
5. **Source cards**: After response completes, source cards with thumbnails appear in the right panel
6. **Source links work**: YouTube links in source cards open correct videos
7. **Dynamic sources**: Ask "What's the greatest comeback?" → source cards update to show the games mentioned in the response
8. **Mode switching**: Toggle to /sql mode, ask stats question → response comes from SQL engine
9. **Clear chat**: Clear button resets conversation history
10. **Fallback**: If router fails (malformed JSON from LLM), falls back to vector search (same as cli.py behavior)

---

## Decisions

- **Tailwind via CDN (Play CDN)**: No build step. Acceptable for local-only app. If later deploying, switch to Tailwind CLI.
- **Session = in-memory dict**: No persistence needed for local use. Refreshing page starts fresh conversation (acceptable).
- **Single user assumption**: No auth, no concurrent session isolation needed. If multiple tabs, each gets own session via cookie.
- **YouTube thumbnails via img.youtube.com**: Free, no API key needed, works for all public videos. Use `hqdefault.jpg` (480x360) as default.
- **Streaming via SSE (not WebSocket)**: Simpler, unidirectional (server→client) which is all we need. htmx SSE extension handles connection.
- **No modifications to query_engine.py**: Web app imports and uses existing engines as-is.
