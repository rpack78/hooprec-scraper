"""
app.py — FastAPI web application for RecHoop RAG Chat.

Provides:
- Landing page with latest 1v1 games
- Streaming chat via Server-Sent Events (SSE)
- Source citations with YouTube thumbnails/links
- htmx-powered dynamic partials

Usage:
    python -m rag.web.app
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from rag.web.db import get_latest_games, get_top_comments, get_game_count

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rag-web")

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

app = FastAPI(title="RecHoop Chat", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=uuid.uuid4().hex)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)


# ---------------------------------------------------------------------------
# Engine management (lazy init, per-session chat history)
# ---------------------------------------------------------------------------

_engines_ready = False
_router_engine = None
_vector_engine = None
_sql_engine = None

# session_id -> {"chat_engine": ..., "mode": "auto"|"sql"|"vector"}
_sessions: dict[str, dict] = {}


def _init_engines():
    """Lazily initialize the query engines on first request."""
    global _engines_ready, _router_engine, _vector_engine, _sql_engine

    if _engines_ready:
        return

    from rag.query_engine import (
        build_router_query_engine,
        build_vector_query_engine,
        get_llm,
        get_sql_query_engine,
        get_vector_query_engine,
    )

    get_llm()
    _router_engine = build_router_query_engine()
    vector_index = build_vector_query_engine()
    _vector_engine = get_vector_query_engine(vector_index)
    _sql_engine = get_sql_query_engine()
    _engines_ready = True


def _get_session(request: Request) -> dict:
    """Get or create a session dict for the current user."""
    sid = request.session.get("sid")
    if not sid or sid not in _sessions:
        sid = uuid.uuid4().hex
        request.session["sid"] = sid

    if sid not in _sessions:
        from llama_index.core.chat_engine import CondenseQuestionChatEngine
        from rag.query_engine import get_llm

        _init_engines()
        chat_engine = CondenseQuestionChatEngine.from_defaults(
            query_engine=_router_engine,
            llm=get_llm(),
        )
        _sessions[sid] = {"chat_engine": chat_engine, "mode": "auto"}

    return _sessions[sid]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_video_id(url: str | None) -> str:
    """Extract YouTube video ID from a URL."""
    if not url:
        return ""
    m = re.search(r"[?&]v=([^&]+)", url)
    return m.group(1) if m else ""


def _build_source_cards(source_nodes) -> list[dict]:
    """Extract source card data from response source nodes."""
    cards = []
    seen = set()
    for node in source_nodes:
        meta = node.metadata or {}
        source_file = meta.get("source_file", "")
        if source_file in seen:
            continue
        seen.add(source_file)

        youtube_url = meta.get("youtube_url", "")
        video_id = _extract_video_id(youtube_url)
        thumbnail_url = (
            f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg" if video_id else ""
        )

        cards.append(
            {
                "player1": meta.get("player1", ""),
                "player2": meta.get("player2", ""),
                "youtube_url": youtube_url,
                "video_id": video_id,
                "thumbnail_url": thumbnail_url,
                "section": meta.get("section", ""),
                "match_date": meta.get("match_date", ""),
                "channel": meta.get("channel", ""),
                "views": meta.get("views"),
                "score": round(getattr(node, "score", 0) or 0, 3),
                "snippet": node.get_content()[:180].replace("\n", " "),
            }
        )
    return cards


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    games = get_latest_games(limit=12)
    game_count = get_game_count()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "games": games, "game_count": game_count},
    )


@app.get("/api/games/latest", response_class=HTMLResponse)
async def games_latest(request: Request, limit: int = 12):
    games = get_latest_games(limit=limit)
    return templates.TemplateResponse(
        "partials/game_cards.html", {"request": request, "games": games}
    )


@app.get("/api/games/{video_id}/comments", response_class=HTMLResponse)
async def game_comments(request: Request, video_id: str):
    comments = get_top_comments(video_id, limit=5)
    return templates.TemplateResponse(
        "partials/comments.html", {"request": request, "comments": comments}
    )


@app.post("/api/chat")
async def chat(request: Request):
    """Stream a chat response via Server-Sent Events."""
    body = await request.json()
    message = body.get("message", "").strip()
    if not message:
        return Response(status_code=400, content="Empty message")

    session = _get_session(request)
    mode = session["mode"]

    async def event_stream():
        try:
            _init_engines()
            source_nodes = []

            # Run the blocking LLM call in a thread
            if mode == "sql":
                response = await asyncio.to_thread(_sql_engine.query, message)
                text = str(response)
                # Yield the full text in small chunks to simulate streaming
                for i in range(0, len(text), 20):
                    chunk = text[i : i + 20]
                    yield f"event: token\ndata: {json.dumps(chunk)}\n\n"
                    await asyncio.sleep(0.01)
                if hasattr(response, "source_nodes"):
                    source_nodes = response.source_nodes

            elif mode == "vector":
                response = await asyncio.to_thread(_vector_engine.query, message)
                text = str(response)
                for i in range(0, len(text), 20):
                    chunk = text[i : i + 20]
                    yield f"event: token\ndata: {json.dumps(chunk)}\n\n"
                    await asyncio.sleep(0.01)
                if hasattr(response, "source_nodes"):
                    source_nodes = response.source_nodes

            else:
                # Auto mode — try router with streaming, fallback to vector
                chat_engine = session["chat_engine"]
                try:
                    response = await asyncio.to_thread(
                        chat_engine.stream_chat, message
                    )
                    for token in response.response_gen:
                        yield f"event: token\ndata: {json.dumps(token)}\n\n"
                    if hasattr(response, "source_nodes"):
                        source_nodes = response.source_nodes
                except Exception as e:
                    log.warning("Router failed (%s), falling back to vector", e)
                    yield f"event: token\ndata: {json.dumps('(auto-routing failed, using vector search) ')}\n\n"
                    response = await asyncio.to_thread(
                        _vector_engine.query, message
                    )
                    text = str(response)
                    for i in range(0, len(text), 20):
                        chunk = text[i : i + 20]
                        yield f"event: token\ndata: {json.dumps(chunk)}\n\n"
                        await asyncio.sleep(0.01)
                    if hasattr(response, "source_nodes"):
                        source_nodes = response.source_nodes

            # Send source cards as a single event
            cards = _build_source_cards(source_nodes)
            yield f"event: sources\ndata: {json.dumps(cards)}\n\n"

            # Done signal
            yield "event: done\ndata: {}\n\n"

        except Exception as exc:
            log.exception("Chat stream error")
            yield f"event: error\ndata: {json.dumps(str(exc))}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/chat/clear")
async def chat_clear(request: Request):
    session = _get_session(request)
    session["chat_engine"].reset()
    return {"status": "ok"}


@app.post("/api/chat/mode/{mode}")
async def chat_mode(request: Request, mode: str):
    if mode not in ("auto", "sql", "vector"):
        return Response(status_code=400, content="Invalid mode")
    session = _get_session(request)
    session["mode"] = mode
    return {"status": "ok", "mode": mode}


# ---------------------------------------------------------------------------
# Data refresh pipeline
# ---------------------------------------------------------------------------

_refresh_running = False


@app.post("/api/ingest/refresh")
async def ingest_refresh():
    """Run the full data refresh pipeline via SSE progress stream.

    Steps:
      1. Phase 1 — Scrape hooprec.com for new matches
      2. Phase 2 — YouTube metadata + comments refresh
      3. Phase 3 — Ingest new markdown into ChromaDB
    """
    global _refresh_running
    if _refresh_running:
        return Response(status_code=409, content="Refresh already in progress")

    from rag.config import PROJECT_ROOT

    python = sys.executable

    async def _run_step(label: str, cmd: list[str], cwd: str | Path):
        """Run a subprocess, yield SSE progress lines."""
        yield f"event: progress\ndata: {json.dumps({'step': label, 'status': 'running'})}\n\n"
        # Disable rich/colorful output and force UTF-8 to prevent
        # UnicodeEncodeError when stdout is piped on Windows.
        env = {
            **os.environ,
            "NO_COLOR": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONUTF8": "1",
        }
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd),
            env=env,
        )
        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").rstrip()
            if line:
                yield f"event: log\ndata: {json.dumps({'step': label, 'line': line})}\n\n"
        await proc.wait()
        ok = proc.returncode == 0
        yield f"event: progress\ndata: {json.dumps({'step': label, 'status': 'done' if ok else 'error', 'code': proc.returncode})}\n\n"
        if not ok:
            raise RuntimeError(f"{label} failed with exit code {proc.returncode}")

    async def event_stream():
        global _refresh_running
        _refresh_running = True
        try:
            # Phase 1 — HoopRec scraper
            async for msg in _run_step(
                "Phase 1: Scrape hooprec.com",
                [python, "hooprec_master_ingest.py"],
                PROJECT_ROOT / "hooprec-ingest",
            ):
                yield msg

            # Phase 2 — YouTube: fetch transcripts, comments, metadata for new videos
            async for msg in _run_step(
                "Phase 2: YouTube ingest",
                [python, "youtube_ingest.py"],
                PROJECT_ROOT / "youtube-ingest",
            ):
                yield msg

            # Phase 3 — ChromaDB ingest (new files only)
            async for msg in _run_step(
                "Phase 3: ChromaDB ingest",
                [python, "-m", "rag.ingest"],
                PROJECT_ROOT,
            ):
                yield msg

            yield f"event: done\ndata: {json.dumps({'status': 'ok'})}\n\n"

        except Exception as exc:
            log.exception("Refresh pipeline error")
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
        finally:
            _refresh_running = False

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.getenv("RAG_WEB_PORT", "8000"))
    uvicorn.run(
        "rag.web.app:app",
        host="127.0.0.1",
        port=port,
        reload=False,
        log_level="info",
    )
