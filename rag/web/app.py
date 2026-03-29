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

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from rag.web.db import (
    get_latest_games,
    get_top_comments,
    get_game_count,
    ensure_web_tables,
    mark_watched,
    unmark_watched,
    get_watched,
    save_google_tokens,
    get_google_tokens,
    clear_google_tokens,
    video_exists,
    get_match_by_video_id,
    create_match_from_discovery,
    get_player_aliases,
    add_player_alias,
    remove_player_alias,
)

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_web_tables()
    _load_player_names()          # always — no Ollama needed
    from rag.web.db import backfill_controversy_scores
    await asyncio.to_thread(backfill_controversy_scores)
    asyncio.create_task(_warmup_ollama())
    from rag.config import PRELOAD_SUGGESTIONS
    if PRELOAD_SUGGESTIONS:
        asyncio.create_task(_preload_suggested())
    yield


app = FastAPI(title="RecHoop Chat", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=uuid.uuid4().hex)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals["cache_bust"] = uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Engine management (lazy init, per-session chat history)
# ---------------------------------------------------------------------------

_engines_ready = False
_vector_index = None     # kept so we can create filtered retrievers on demand
_vector_engine = None
_sql_engine = None
_player_names: list[str] = []   # sorted longest-first for greedy matching
_player_aliases: dict[str, list[str]] = {}

# session_id -> {"mode": "auto"|"sql"|"vector"}
_sessions: dict[str, dict] = {}

# Pre-cached responses for suggested prompts
_preloaded_cache: dict[str, dict] = {}  # message -> {"text": ..., "sources": [...]}
_SUGGESTED_PROMPTS = [
    "What are the most exciting games to watch?",
    "What is the greatest comeback?",
    "Who do fans think is the best 1v1 player?",
    "What games had the most trash talk?",
]

# Stats-related keywords that hint at SQL mode
_STATS_KEYWORDS = re.compile(
    r"\b(wins?|loss(?:es)?|records?|how many|scores?|stats|"
    r"view count|most viewed|most watched|least viewed|"
    r"played each other|head to head|match(?:es)?)\b",
    re.IGNORECASE,
)

# List-all / chronological keywords → should go to SQL for comprehensive results
_LIST_KEYWORDS = re.compile(
    r"\b(all\s+(the\s+)?games|every\s+game|chronological|"
    r"in\s+order|list\s+(all|every|the)|full\s+list|show\s+me\s+all|"
    r"show\s+me\s+(games|videos)|(?:games?|videos?)\s+(featuring|with|of))\b",
    re.IGNORECASE,
)

# Fast path for simple DB records (player stats, h2h, leaderboards)
_PLAYER_STATS_KW = re.compile(
    r"\b(how many (games?|times|wins|loss)|"
    r"record|stats|win (rate|percentage|pct)|"
    r"games? played|undefeated|winless)\b",
    re.IGNORECASE,
)
_H2H_KW = re.compile(
    r"\b(vs\.?|versus|head.to.head|h2h|played each other|"
    r"played against|face[ds]?|match.?up)\b",
    re.IGNORECASE,
)
_LEADERBOARD_KW = re.compile(
    r"\b(who has the most|most (wins|losses|games|viewed|watched|popular)|"
    r"best record|highest win|top (players?|records?)|"
    r"winningest|leaderboard|rankings?)\b",
    re.IGNORECASE,
)
_CONTROVERSY_KW = re.compile(
    r"\b(bad (call|calls|ref|refs|reffing)|refs? (mess(?:ed)?|blow|blew|screw(?:ed)?|trippin|tweakin)|"
    r"controversial (call|calls|ref|game)|blown call|biased ref|"
    r"refs? made|bad ref(?:ereeing)?|corrupt ref|"
    r"travel call|phantom (foul|call|timeout)|"
    r"ref(?:s)? (ruined|cost|cheated|stole)|"
    r"should.ve been|robbery|robbed|rigged|"
    r"games? (with|where|that had) (bad|terrible|awful|horrible|trash|the worst) (ref|refs|call|calls|reffing|officiating)|"
    r"(bad|terrible|awful|horrible|trash|worst) (ref|refs|call|calls|reffing|officiating))\b",
    re.IGNORECASE,
)

_ALIAS_STOPWORDS = {
    "a", "an", "and", "ant", "at", "best", "for", "games", "game", "how",
    "i", "in", "is", "it", "me", "more", "of", "on", "or", "record",
    "show", "stats", "the", "to", "vs", "what", "who", "will",
}

# Manual aliases are now stored in the player_aliases DB table.
# Use the /api/aliases endpoints to manage them.


def _normalize_player_text(text: str) -> str:
    text = text.lower().replace("'s", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _build_player_aliases(player_names: list[str]) -> dict[str, list[str]]:
    aliases: dict[str, set[str]] = {}
    # Count how many players share each normalized token
    token_counts: dict[str, int] = {}
    short_token_counts: dict[str, int] = {}

    for name in player_names:
        normalized = _normalize_player_text(name)
        if not normalized:
            continue
        aliases.setdefault(normalized, set()).add(name)
        tokens = normalized.split()
        seen_tokens: set[str] = set()
        for t in tokens:
            if t not in seen_tokens:
                token_counts[t] = token_counts.get(t, 0) + 1
                seen_tokens.add(t)

        original_tokens = re.findall(r"[A-Za-z0-9']+", name)
        for index, token in enumerate(original_tokens):
            token_norm = _normalize_player_text(token)
            if not token_norm:
                continue
            if token.lower() == "aka" and index + 1 < len(original_tokens):
                aka_norm = _normalize_player_text(original_tokens[index + 1])
                if aka_norm:
                    short_token_counts[aka_norm] = short_token_counts.get(aka_norm, 0) + 1
            if 2 <= len(token_norm) <= 4 and token.upper() == token and token_norm not in _ALIAS_STOPWORDS:
                short_token_counts[token_norm] = short_token_counts.get(token_norm, 0) + 1

    for name in player_names:
        normalized = _normalize_player_text(name)
        tokens = normalized.split()
        if not tokens:
            continue

        # Register any unique token (>= 3 chars, not a stopword) as an alias
        if len(tokens) > 1:
            for t in tokens:
                if (
                    len(t) >= 3
                    and token_counts.get(t) == 1
                    and t not in _ALIAS_STOPWORDS
                ):
                    aliases.setdefault(t, set()).add(name)

        original_tokens = re.findall(r"[A-Za-z0-9']+", name)
        for index, token in enumerate(original_tokens):
            token_norm = _normalize_player_text(token)
            if not token_norm:
                continue

            aka_alias = None
            if token.lower() == "aka" and index + 1 < len(original_tokens):
                aka_alias = _normalize_player_text(original_tokens[index + 1])
            if aka_alias and short_token_counts.get(aka_alias) == 1:
                aliases.setdefault(aka_alias, set()).add(name)

            if (
                2 <= len(token_norm) <= 4
                and token.upper() == token
                and token_norm not in _ALIAS_STOPWORDS
                and short_token_counts.get(token_norm) == 1
            ):
                aliases.setdefault(token_norm, set()).add(name)

    # Merge DB-stored aliases (override auto-detected ones for these keys)
    db_aliases = get_player_aliases()
    for alias_key, canonical_names in db_aliases.items():
        aliases[alias_key] = set(canonical_names)

    return {alias: sorted(names, key=len, reverse=True) for alias, names in aliases.items()}


def _load_player_names():
    """Load player names and aliases from SQLite — no Ollama needed.
    Called eagerly on startup so fast DB paths work immediately."""
    global _player_names, _player_aliases
    import sqlite3
    from rag.config import DB_PATH
    try:
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute("SELECT name FROM players ORDER BY LENGTH(name) DESC").fetchall()
        _player_names = [r[0] for r in rows]
        _player_aliases = _build_player_aliases(_player_names)
        conn.close()
        log.warning("Loaded %d players and %d aliases", len(_player_names), len(_player_aliases))
    except Exception:
        log.warning("Could not load player names — smart routing disabled")


def _init_engines():
    """Lazily initialize the query engines on first request."""
    global _engines_ready, _vector_index, _vector_engine, _sql_engine, _player_names, _player_aliases

    if _engines_ready:
        return

    from rag.query_engine import (
        build_vector_query_engine,
        get_llm,
        get_sql_query_engine,
        get_vector_query_engine,
    )

    get_llm()
    _vector_index = build_vector_query_engine()
    _vector_engine = get_vector_query_engine(_vector_index)
    _sql_engine = get_sql_query_engine()

    _engines_ready = True


def _detect_players(query: str) -> list[str]:
    """Return canonical player names found in the query.

    Matches full names first, then unique short-name aliases like "Rob" for
    "Rob Colon", while also handling possessives such as "Rob's".
    Falls back to unique-prefix matching for short tokens like "Nas" → "Nash".
    """
    normalized_query = _normalize_player_text(query)
    found: list[str] = []
    seen: set[str] = set()

    for alias, names in sorted(_player_aliases.items(), key=lambda item: len(item[0]), reverse=True):
        pattern = r'(?<![a-z0-9])' + re.escape(alias) + r'(?![a-z0-9])'
        if not re.search(pattern, normalized_query):
            continue
        for name in names:
            if name not in seen:
                found.append(name)
                seen.add(name)

    # Prefix fallback: if no exact match, check if any query token is a unique
    # prefix of an alias (e.g. "nas" → "nash")
    if not found:
        query_tokens = [t for t in normalized_query.split() if len(t) >= 3 and t not in _ALIAS_STOPWORDS]
        all_aliases = list(_player_aliases.keys())
        for qt in query_tokens:
            prefix_matches: list[str] = []
            for alias in all_aliases:
                if alias.startswith(qt) and alias != qt:
                    prefix_matches.append(alias)
            if len(prefix_matches) == 1:
                for name in _player_aliases[prefix_matches[0]]:
                    if name not in seen:
                        found.append(name)
                        seen.add(name)

    return found


def _build_filtered_vector_engine(player_names: list[str]):
    """Create a vector query engine with metadata filters for the given players."""
    from llama_index.core.vector_stores import (
        FilterCondition,
        FilterOperator,
        MetadataFilter,
        MetadataFilters,
    )
    from rag.query_engine import get_llm
    from rag.config import TOP_K

    filters_list = []
    for name in player_names:
        filters_list.append(
            MetadataFilter(key="player1", value=name, operator=FilterOperator.EQ)
        )
        filters_list.append(
            MetadataFilter(key="player2", value=name, operator=FilterOperator.EQ)
        )

    filters = MetadataFilters(filters=filters_list, condition=FilterCondition.OR)

    return _vector_index.as_query_engine(
        llm=get_llm(),
        similarity_top_k=TOP_K,
        filters=filters,
    )


def _get_session(request: Request) -> dict:
    """Get or create a session dict for the current user."""
    sid = request.session.get("sid")
    if not sid or sid not in _sessions:
        sid = uuid.uuid4().hex
        request.session["sid"] = sid

    if sid not in _sessions:
        _sessions[sid] = {"mode": "auto"}

    return _sessions[sid]


# ---------------------------------------------------------------------------
# Fast DB responses — bypass LLM for simple factual queries
# ---------------------------------------------------------------------------


def _try_fast_db_response(
    message: str, players: list[str], view_mode: str
) -> str | None:
    """Return a formatted markdown response if the query can be answered
    directly from the database, or None to fall through to the LLM."""
    from rag.web.db import get_player_stats, get_head_to_head, get_leaderboard

    show_outcome = view_mode == "stats"

    # ── Head-to-head: two players + vs/h2h keywords ──
    if len(players) == 2 and _H2H_KW.search(message):
        h2h = get_head_to_head(players[0], players[1])
        if h2h["total_games"] == 0:
            return f"**{players[0]}** and **{players[1]}** have never played each other in the database."
        lines = [
            f"**{players[0]} vs {players[1]}** — {h2h['total_games']} game{'s' if h2h['total_games'] != 1 else ''} on record:\n",
        ]
        if show_outcome:
            lines.append(f"- **{players[0]}**: {h2h['a_wins']} win{'s' if h2h['a_wins'] != 1 else ''}")
            lines.append(f"- **{players[1]}**: {h2h['b_wins']} win{'s' if h2h['b_wins'] != 1 else ''}")
        else:
            lines.append("Switch to 📊 **Stats** mode to see the win/loss breakdown.")
        return "\n".join(lines)

    # ── Player stats: one player + stats keywords ──
    if len(players) >= 1 and _PLAYER_STATS_KW.search(message):
        results = []
        for name in players:
            stats = get_player_stats(name)
            if not stats:
                results.append(f"No player named **{name}** found in the database.")
                continue
            total = stats["total_games"]
            if show_outcome:
                w, l = stats["wins"], stats["losses"]
                pct = round(100 * w / max(total, 1), 1)
                results.append(
                    f"**{stats['name']}** has played **{total} game{'s' if total != 1 else ''}** — "
                    f"**{w}W–{l}L** ({pct}% win rate)"
                )
            else:
                results.append(
                    f"**{stats['name']}** has played **{total} game{'s' if total != 1 else ''}**. "
                    "Switch to 📊 **Stats** mode to see the win/loss breakdown."
                )
        return "\n\n".join(results)

    # ── Ref controversy: no specific player + controversy keywords ──
    if not players and _CONTROVERSY_KW.search(message):
        from rag.web.db import get_controversy_games
        rows = get_controversy_games(limit=10)
        if not rows:
            return "No games with notable ref controversy found in the database."
        lines = ["**Games with the most ref/officiating complaints from fans:**\n"]
        for i, r in enumerate(rows, 1):
            p1, p2 = r["player1_name"], r["player2_name"]
            vid = r.get("video_id", "")
            score = r.get("ref_controversy_score", 0)
            watch_link = f" {{{{watch:{vid}}}}}" if vid else ""
            lines.append(
                f"{i}. **{p1} vs {p2}** ({r.get('match_date', '')}) "
                f"— {score} complaint{'s' if score != 1 else ''} in comments{watch_link}"
            )
        return "\n".join(lines)

    # ── Leaderboards: no specific player + leaderboard keywords ──
    if not players and _LEADERBOARD_KW.search(message):
        msg_lower = message.lower()

        if "most viewed" in msg_lower or "most watched" in msg_lower or "most popular" in msg_lower:
            rows = get_leaderboard("most_viewed", limit=10)
            if not rows:
                return "No view count data available."
            lines = ["**Most Viewed Games:**\n"]
            for i, r in enumerate(rows, 1):
                views = f"{r['view_count']:,}"
                p1, p2 = r["player1_name"], r["player2_name"]
                vid = r.get("video_id", "")
                watch_link = f" {{{{watch:{vid}}}}}" if vid else ""
                lines.append(f"{i}. **{p1} vs {p2}** — {views} views ({r.get('channel_name', '')}){watch_link}")
            return "\n".join(lines)

        if "most loss" in msg_lower:
            cat = "most_losses"
            title = "Most Losses"
        elif "most games" in msg_lower or "most played" in msg_lower:
            cat = "most_games"
            title = "Most Games Played"
        elif "best record" in msg_lower or "highest win" in msg_lower or "winningest" in msg_lower:
            cat = "best_record"
            title = "Best Win Rate (min 3 games)"
        else:
            cat = "most_wins"
            title = "Most Wins"

        rows = get_leaderboard(cat, limit=10)
        if not rows:
            return "No player data available."
        lines = [f"**{title}:**\n"]
        for i, r in enumerate(rows, 1):
            if show_outcome:
                lines.append(
                    f"{i}. **{r['name']}** — {r['wins']}W–{r['losses']}L"
                    f" ({r['total_games']} games, {r['win_pct']}%)"
                )
            else:
                if cat == "most_games":
                    lines.append(f"{i}. **{r['name']}** — {r['total_games']} games")
                else:
                    lines.append(f"{i}. **{r['name']}**")
        return "\n".join(lines)

    return None


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

        # Skip nodes with no meaningful metadata (e.g. SQL results)
        player1 = meta.get("player1", "")
        player2 = meta.get("player2", "")
        if not player1 and not player2:
            continue

        youtube_url = meta.get("youtube_url", "")
        video_id = _extract_video_id(youtube_url)
        thumbnail_url = (
            f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg" if video_id else ""
        )

        # Build a short summary from the title or content
        title = meta.get("title", "")
        content_preview = node.get_content()[:300].replace("\n", " ").strip()
        # Use the YouTube title as the summary if available, otherwise a content snippet
        if title:
            summary = title
        elif content_preview:
            # Truncate to ~100 chars at a word boundary
            if len(content_preview) > 100:
                summary = content_preview[:100].rsplit(" ", 1)[0] + "…"
            else:
                summary = content_preview
        else:
            summary = ""

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
                "summary": summary,
            }
        )
    return cards


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

# YouTube channel avatar URLs (manually mapped for top channels)
_CHANNEL_ICONS: dict[str, str] = {
    "The Next Chapter": "https://yt3.googleusercontent.com/ytc/AIdro_kQEslYkBm2LO7vjJHVh2vjYFaOjbuVfiOlCfTlFJk7VJM=s88-c-k-c0x00ffffff-no-rj",
    "Ballislife": "https://yt3.googleusercontent.com/ytc/AIdro_m7LoGQz6sN76YYFp-tKZfuaHIPMY7J7fibJYG4b0pu3w=s88-c-k-c0x00ffffff-no-rj",
    "Off The Dribble": "https://yt3.googleusercontent.com/WgN7fp4MFNxTkXC_LYqPy7g5axOMGz6KLj1v2e7QXDK2pS-QQET1-VDjGd3teri2lcpH_sMIog=s88-c-k-c0x00ffffff-no-rj",
    "BallislifeHoops": "https://yt3.googleusercontent.com/qfBJaxAqUTEM0Z3AdWkFqEG5DqSMrTnecYa4_XxwjGTDhSNAFqPe7J0UnGWS4wqN9Cf5jxZLFQ=s88-c-k-c0x00ffffff-no-rj",
    "Uncle Skoob": "https://yt3.googleusercontent.com/JTFKnCaL7o81xj6c_Mu-e4Kps8cfLSV_29W_c6pWY59CtJuDPjC_v2IqNxTrZ40JgUWEfIlL=s88-c-k-c0x00ffffff-no-rj",
    "Junes League": "https://yt3.googleusercontent.com/LhSzVRXv2j4eiDCFdRO9w_KBXl8rqB3rfbwKlAfvXVWh4BOqeBvd8sMcXL3RQ8G6d_RDM8gKYQ=s88-c-k-c0x00ffffff-no-rj",
    "FreeSmokeTour": "https://yt3.googleusercontent.com/EY-t8wkxBZ5gxFLDqGpN_lPMyyLVG5IFv-4R8RkUGk8KUGwR01aqigxEd0Do-cLJp9GhZJwb=s88-c-k-c0x00ffffff-no-rj",
}


# ---------------------------------------------------------------------------
# Preloading — warm the cache for suggested prompts on startup
# ---------------------------------------------------------------------------

_preload_started = False
_PRELOAD_CACHE_FILE = Path(__file__).parent.parent.parent / "data" / "db" / "preload_cache.json"


def _load_preload_cache() -> bool:
    """Load cached suggestions from disk. Returns True if cache is valid."""
    if not _PRELOAD_CACHE_FILE.exists():
        return False
    try:
        data = json.loads(_PRELOAD_CACHE_FILE.read_text(encoding="utf-8"))
        # Invalidate if game count changed (new data was ingested)
        stored_count = data.get("game_count", 0)
        current_count = get_game_count()
        if stored_count != current_count:
            log.info("Preload cache stale (games: %d → %d), regenerating", stored_count, current_count)
            return False
        entries = data.get("entries", {})
        for prompt_text in _SUGGESTED_PROMPTS:
            if prompt_text in entries:
                _preloaded_cache[prompt_text] = entries[prompt_text]
        log.info("Loaded %d cached suggestions from disk", len(_preloaded_cache))
        return len(_preloaded_cache) == len(_SUGGESTED_PROMPTS)
    except Exception as e:
        log.warning("Failed to load preload cache: %s", e)
        return False


def _save_preload_cache() -> None:
    """Persist preloaded suggestions to disk."""
    try:
        data = {
            "game_count": get_game_count(),
            "entries": {k: v for k, v in _preloaded_cache.items() if k in _SUGGESTED_PROMPTS},
        }
        _PRELOAD_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PRELOAD_CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        log.info("Saved preload cache to disk (%d entries)", len(data["entries"]))
    except Exception as e:
        log.warning("Failed to save preload cache: %s", e)


async def _preload_suggested():
    """Run suggested prompts in background to cache responses."""
    global _preload_started
    if _preload_started:
        return
    _preload_started = True

    # Try loading from disk first
    if _load_preload_cache():
        return

    log.info("Preloading %d suggested prompts in background…", len(_SUGGESTED_PROMPTS))
    for prompt_text in _SUGGESTED_PROMPTS:
        if prompt_text in _preloaded_cache:
            continue
        try:
            _init_engines()
            response = await asyncio.to_thread(_vector_engine.query, prompt_text)
            text = str(response)
            source_nodes = getattr(response, "source_nodes", [])
            cards = _build_source_cards(source_nodes)
            _preloaded_cache[prompt_text] = {"text": text, "sources": cards}
            log.info("  Cached: %s (%d chars)", prompt_text[:40], len(text))
        except Exception as e:
            log.warning("  Failed to preload '%s': %s", prompt_text[:40], e)

    log.info("Preloading complete (%d/%d cached)", len(_preloaded_cache), len(_SUGGESTED_PROMPTS))
    _save_preload_cache()


async def _warmup_ollama():
    """Send a tiny prompt to Ollama so the model is loaded and ready."""
    try:
        _init_engines()
        from rag.query_engine import get_llm
        llm = get_llm()
        await asyncio.to_thread(llm.complete, "hi")
        log.warning("Ollama warmup complete — model loaded")
    except Exception:
        log.warning("Ollama warmup failed — first query may be slow")


@app.get("/favicon.ico")
async def favicon():
    return Response(
        content=b'\x00\x00\x01\x00\x01\x00\x01\x01\x00\x00\x01\x00\x18\x00\x30\x00\x00\x00\x16\x00\x00\x00\x28\x00\x00\x00\x01\x00\x00\x00\x02\x00\x00\x00\x01\x00\x18\x00\x00\x00\x00\x00\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x16\x97\xf9\x00\x00\x00\x00',
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.get("/api/channel-icon/{channel_name}")
async def channel_icon(channel_name: str):
    """Redirect to the YouTube channel avatar image."""
    from fastapi.responses import RedirectResponse
    url = _CHANNEL_ICONS.get(channel_name)
    if url:
        return RedirectResponse(url=url, status_code=302)
    # Fallback: transparent 1x1 pixel
    return Response(
        content=b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b',
        media_type="image/gif",
        headers={"Cache-Control": "public, max-age=86400"},
    )


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
    view_mode = body.get("view_mode", "watch")  # "watch" or "stats"
    if not message:
        return Response(status_code=400, content="Empty message")

    session = _get_session(request)
    mode = session["mode"]

    async def event_stream():
        try:
            _init_engines()
            source_nodes = []

            # Check preloaded cache for instant responses to suggested prompts
            cached = _preloaded_cache.get(message)
            if cached and mode in ("auto", "vector"):
                yield f"event: route\ndata: {json.dumps('⚡ cached')}\n\n"
                text = cached["text"]
                for i in range(0, len(text), 40):
                    chunk = text[i : i + 40]
                    yield f"event: token\ndata: {json.dumps(chunk)}\n\n"
                    await asyncio.sleep(0.005)
                yield f"event: sources\ndata: {json.dumps(cached['sources'])}\n\n"
                yield "event: done\ndata: {}\n\n"
                return

            # Determine which engine to use
            if mode == "sql":
                engine = _sql_engine
                route_label = "sql"
            elif mode == "vector":
                engine = _vector_engine
                route_label = "vector"
            else:
                # Controversy queries must be checked before player detection —
                # common words like "bad" can spuriously match player aliases.
                if _CONTROVERSY_KW.search(message):
                    players = []
                    has_stats = False
                    has_list = False
                else:
                    # Smart auto-routing: detect player names and stats keywords
                    players = _detect_players(message)
                    has_stats = bool(_STATS_KEYWORDS.search(message))
                    has_list = bool(_LIST_KEYWORDS.search(message))

                # ── Fast DB path: answer directly without LLM ──
                fast = _try_fast_db_response(message, players, view_mode)
                if fast is not None:
                    yield f"event: route\ndata: {json.dumps('⚡ db (instant)')}\n\n"
                    for i in range(0, len(fast), 40):
                        chunk = fast[i : i + 40]
                        yield f"event: token\ndata: {json.dumps(chunk)}\n\n"
                        await asyncio.sleep(0.005)
                    # Attach source cards for player queries
                    cards = []
                    if players:
                        from rag.web.db import get_player_games
                        db_games = get_player_games(players, limit=10)
                        for g in db_games:
                            vid = g.get("video_id", "")
                            cards.append({
                                "player1": g.get("player1_name", ""),
                                "player2": g.get("player2_name", ""),
                                "youtube_url": g.get("youtube_url", ""),
                                "video_id": vid,
                                "thumbnail_url": f"https://img.youtube.com/vi/{vid}/hqdefault.jpg" if vid else "",
                                "match_date": g.get("match_date", ""),
                                "channel": g.get("channel_name", ""),
                                "views": g.get("view_count"),
                                "score": 0,
                                "summary": g.get("title", ""),
                            })
                    yield f"event: sources\ndata: {json.dumps(cards)}\n\n"
                    yield "event: done\ndata: {}\n\n"
                    return

                if has_list and players:
                    # "Show me all games with X" → direct DB query, skip LLM
                    route_label = "sql (full list)"
                    note = f"⚡ {route_label}"
                    yield f"event: route\ndata: {json.dumps(note)}\n\n"

                    # Scores reveal the winner — in watch mode always hide,
                    # in stats mode show, and also show if the user explicitly asks.
                    _OUTCOME_KW = re.compile(
                        r"\b(who\s+won|winner|win|wins|record|results?|W-L|losses?|scores?|final)\b",
                        re.IGNORECASE,
                    )
                    show_outcome = view_mode == "stats" or bool(_OUTCOME_KW.search(message))

                    from rag.web.db import get_player_games
                    db_games = get_player_games(players, limit=50)
                    player_label = " & ".join(players)
                    if not db_games:
                        text = f"No games found for {player_label}."
                    else:
                        players_lower = {p.lower() for p in players}
                        lines = [f"Found **{len(db_games)} games** for {player_label}:\n"]
                        for g in db_games:
                            p1, p2 = g.get("player1_name", ""), g.get("player2_name", "")
                            # Put the queried player on the left for easy scanning
                            if p2.lower() in players_lower and p1.lower() not in players_lower:
                                p1, p2 = p2, p1
                            d = g.get("match_date", "")
                            if show_outcome:
                                s1, s2 = g.get("player1_score", ""), g.get("player2_score", "")
                                w = g.get("winner_name", "")
                                outcome_tag = f" {s1}–{s2}"
                                if w:
                                    outcome_tag += f" — **{w} wins**"
                            else:
                                outcome_tag = ""
                            vid = g.get("video_id", "")
                            watch_link = f" {{{{watch:{vid}}}}}" if vid else ""
                            lines.append(f"- **{p1}**{outcome_tag} **vs** **{p2}** ({d}){watch_link}")
                        text = "\n".join(lines)

                    for i in range(0, len(text), 20):
                        chunk = text[i : i + 20]
                        yield f"event: token\ndata: {json.dumps(chunk)}\n\n"
                        await asyncio.sleep(0.01)

                    cards = []
                    for g in db_games:
                        vid = g.get("video_id", "")
                        cards.append({
                            "player1": g.get("player1_name", ""),
                            "player2": g.get("player2_name", ""),
                            "youtube_url": g.get("youtube_url", ""),
                            "video_id": vid,
                            "thumbnail_url": f"https://img.youtube.com/vi/{vid}/hqdefault.jpg" if vid else "",
                            "match_date": g.get("match_date", ""),
                            "channel": g.get("channel_name", ""),
                            "views": g.get("view_count"),
                            "score": 0,
                            "summary": g.get("title", ""),
                        })
                    yield f"event: sources\ndata: {json.dumps(cards)}\n\n"
                    yield "event: done\ndata: {}\n\n"
                    return

                elif players and has_stats:
                    # Both player names AND stats keywords → try SQL first
                    engine = _sql_engine
                    route_label = "sql"
                elif has_list or has_stats:
                    # Pure stats/list query → SQL
                    engine = _sql_engine
                    route_label = "sql"
                elif players:
                    # Player names found → metadata-filtered vector search
                    engine = _build_filtered_vector_engine(players)
                    route_label = f"vector (filtered: {', '.join(players)})"
                elif has_stats:
                    # Pure stats query → SQL
                    engine = _sql_engine
                    route_label = "sql"
                else:
                    # Default → general vector search
                    engine = _vector_engine
                    route_label = "vector"

                # Emit a small routing note
                note = f"⚡ {route_label}"
                yield f"event: route\ndata: {json.dumps(note)}\n\n"

            # Run the query
            query_text = message
            if view_mode == "watch":
                query_text = (
                    message
                    + "\n\n[IMPORTANT: The user is in spoiler-free Watch mode. "
                    "Do NOT reveal final scores, winners, or outcomes. "
                    "Focus on describing the matchup, atmosphere, and why it's worth watching.]"
                )
            response = await asyncio.to_thread(engine.query, query_text)
            text = str(response)
            for i in range(0, len(text), 20):
                chunk = text[i : i + 20]
                yield f"event: token\ndata: {json.dumps(chunk)}\n\n"
                await asyncio.sleep(0.01)
            if hasattr(response, "source_nodes"):
                source_nodes = response.source_nodes

            # Build source cards — from vector results, or from DB for SQL player queries
            cards = _build_source_cards(source_nodes)
            if not cards and mode in ("auto", "sql"):
                # SQL queries don't return source nodes; fetch player games from DB
                players = _detect_players(message)
                if players:
                    from rag.web.db import get_player_games
                    db_games = get_player_games(players, limit=10)
                    for g in db_games:
                        vid = g.get("video_id", "")
                        cards.append({
                            "player1": g.get("player1_name", ""),
                            "player2": g.get("player2_name", ""),
                            "youtube_url": g.get("youtube_url", ""),
                            "video_id": vid,
                            "thumbnail_url": f"https://img.youtube.com/vi/{vid}/hqdefault.jpg" if vid else "",
                            "match_date": g.get("match_date", ""),
                            "channel": g.get("channel_name", ""),
                            "views": g.get("view_count"),
                            "score": 0,
                            "summary": g.get("title", ""),
                        })

            yield f"event: sources\ndata: {json.dumps(cards)}\n\n"

            # Done signal
            yield "event: done\ndata: {}\n\n"

        except Exception as exc:
            log.exception("Chat stream error")
            msg = str(exc)
            if "timed out" in msg.lower() or "ReadTimeout" in msg:
                msg = "The AI model took too long to respond. Please try again — it should be faster now that the model is loaded."
            yield f"event: error\ndata: {json.dumps(msg)}\n\n"

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
    session["mode"] = "auto"
    return {"status": "ok"}


@app.post("/api/chat/mode/{mode}")
async def chat_mode(request: Request, mode: str):
    if mode not in ("auto", "sql", "vector"):
        return Response(status_code=400, content="Invalid mode")
    session = _get_session(request)
    session["mode"] = mode
    return {"status": "ok", "mode": mode}


# ---------------------------------------------------------------------------
# Player aliases
# ---------------------------------------------------------------------------

@app.get("/api/aliases")
async def list_aliases():
    """Return all player aliases."""
    return get_player_aliases()


@app.post("/api/aliases")
async def create_alias(request: Request):
    """Add a player alias. Body: {"alias": "...", "player_name": "..."}."""
    body = await request.json()
    alias = body.get("alias", "").strip()
    player_name = body.get("player_name", "").strip()
    if not alias or not player_name:
        return Response(status_code=400, content="Both alias and player_name are required")
    added = add_player_alias(alias, player_name)
    if added:
        _reload_aliases()
    return {"status": "ok", "added": added, "alias": alias, "player_name": player_name}


@app.delete("/api/aliases")
async def delete_alias(request: Request):
    """Remove a player alias. Body: {"alias": "...", "player_name": "..."}."""
    body = await request.json()
    alias = body.get("alias", "").strip()
    player_name = body.get("player_name", "").strip()
    if not alias or not player_name:
        return Response(status_code=400, content="Both alias and player_name are required")
    removed = remove_player_alias(alias, player_name)
    if removed:
        _reload_aliases()
    return {"status": "ok", "removed": removed}


def _reload_aliases():
    """Rebuild the player alias lookup after a DB change."""
    _load_player_names()


# ---------------------------------------------------------------------------
# Watch tracking
# ---------------------------------------------------------------------------

@app.get("/api/watch")
async def watch_list():
    """Return all watched video IDs with their dates."""
    return get_watched()


@app.post("/api/watch/{video_id}")
async def watch_mark(video_id: str):
    """Mark a video as watched (today's date)."""
    result = mark_watched(video_id)
    return result


@app.delete("/api/watch/{video_id}")
async def watch_unmark(video_id: str):
    """Remove watched status from a video."""
    removed = unmark_watched(video_id)
    return {"removed": removed}


# ---------------------------------------------------------------------------
# Google OAuth 2.0 — YouTube commenting
# ---------------------------------------------------------------------------

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_YOUTUBE_SCOPES = "https://www.googleapis.com/auth/youtube.force-ssl"


@app.get("/api/auth/status")
async def auth_status():
    """Check if user is signed in with Google."""
    tokens = get_google_tokens()
    if tokens and tokens.get("access_token"):
        return {"signed_in": True, "email": tokens.get("email")}
    return {"signed_in": False}


@app.get("/api/auth/login")
async def auth_login(request: Request):
    """Redirect to Google OAuth consent screen."""
    from rag.config import GOOGLE_CLIENT_ID
    if not GOOGLE_CLIENT_ID:
        html = (
            "<html><body><script>"
            "window.opener && window.opener.postMessage({type:'oauth_error',message:'Google Sign-In is not configured yet.'},'*');"
            "window.close();"
            "</script><p>Google Sign-In is not configured. You can close this window.</p></body></html>"
        )
        return Response(content=html, media_type="text/html")

    from urllib.parse import urlencode
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/auth/callback"

    params = urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _YOUTUBE_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
    })
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"{_GOOGLE_AUTH_URL}?{params}")


@app.get("/api/auth/callback")
async def auth_callback(request: Request, code: str = ""):
    """Handle Google OAuth callback — exchange code for tokens."""
    if not code:
        return Response(status_code=400, content="Missing authorization code")

    from rag.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    import httpx

    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/auth/callback"

    async with httpx.AsyncClient() as client:
        # Exchange code for tokens
        token_resp = await client.post(_GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        if token_resp.status_code != 200:
            log.error("OAuth token exchange failed: %s", token_resp.text)
            return HTMLResponse(
                "<script>window.close();alert('OAuth failed');</script>",
                status_code=400,
            )

        token_data = token_resp.json()
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token", "")
        expires_in = token_data.get("expires_in", 3600)

        from datetime import datetime, timedelta
        expiry = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat()

        # Get user email
        userinfo_resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        email = userinfo_resp.json().get("email", "") if userinfo_resp.status_code == 200 else ""

    save_google_tokens(access_token, refresh_token, expiry, email)

    # Close the popup and notify the opener
    return HTMLResponse("""
        <script>
            if (window.opener) {
                window.opener.postMessage({type: 'oauth_complete'}, '*');
            }
            window.close();
        </script>
    """)


@app.post("/api/auth/logout")
async def auth_logout():
    """Clear stored Google OAuth tokens."""
    clear_google_tokens()
    return {"status": "ok"}


async def _get_valid_access_token() -> str | None:
    """Return a valid access token, refreshing if expired."""
    tokens = get_google_tokens()
    if not tokens or not tokens.get("access_token"):
        return None

    from datetime import datetime
    expiry_str = tokens.get("token_expiry", "")
    if expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str)
            if datetime.utcnow() < expiry:
                return tokens["access_token"]
        except ValueError:
            pass

    # Token expired — try to refresh
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        return None

    from rag.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.post(_GOOGLE_TOKEN_URL, data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
        if resp.status_code != 200:
            return None

        data = resp.json()
        new_access = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        from datetime import timedelta
        new_expiry = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat()
        save_google_tokens(new_access, refresh_token, new_expiry)
        return new_access


# ---------------------------------------------------------------------------
# YouTube comment reply / post
# ---------------------------------------------------------------------------

@app.post("/api/comments/reply")
async def comment_reply(request: Request):
    """Post a reply to a YouTube comment (requires OAuth)."""
    body = await request.json()
    parent_id = body.get("parent_id", "").strip()
    text = body.get("text", "").strip()
    if not parent_id or not text:
        return Response(status_code=400, content="Missing parent_id or text")

    access_token = await _get_valid_access_token()
    if not access_token:
        return Response(status_code=401, content="Not signed in with Google")

    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://www.googleapis.com/youtube/v3/comments",
            params={"part": "snippet"},
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "snippet": {
                    "parentId": parent_id,
                    "textOriginal": text,
                }
            },
        )
        if resp.status_code in (200, 201):
            return {"status": "ok", "comment": resp.json()}
        log.error("YouTube comment reply failed: %s", resp.text)
        return Response(status_code=resp.status_code, content=resp.text)


@app.post("/api/comments/post")
async def comment_post(request: Request):
    """Post a new top-level comment on a YouTube video (requires OAuth)."""
    body = await request.json()
    video_id = body.get("video_id", "").strip()
    text = body.get("text", "").strip()
    if not video_id or not text:
        return Response(status_code=400, content="Missing video_id or text")

    access_token = await _get_valid_access_token()
    if not access_token:
        return Response(status_code=401, content="Not signed in with Google")

    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://www.googleapis.com/youtube/v3/commentThreads",
            params={"part": "snippet"},
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {
                            "textOriginal": text,
                        }
                    },
                }
            },
        )
        if resp.status_code in (200, 201):
            return {"status": "ok", "comment": resp.json()}
        log.error("YouTube comment post failed: %s", resp.text)
        return Response(status_code=resp.status_code, content=resp.text)


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
# Add Video (Phase 4.1)
# ---------------------------------------------------------------------------

_YT_ID_PATTERNS = [
    re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_\-]{11})"),
    re.compile(r"youtube\.com/embed/([A-Za-z0-9_\-]{11})"),
]


def _extract_video_ids_from_text(text: str) -> list[str]:
    """Parse all YouTube video IDs from raw text, deduplicated, order-preserved."""
    seen: set[str] = set()
    result: list[str] = []
    for pat in _YT_ID_PATTERNS:
        for m in pat.finditer(text):
            vid = m.group(1)
            if vid not in seen:
                seen.add(vid)
                result.append(vid)
    return result


_VS_PATTERN = re.compile(
    r"(?:^|[|…\s])(?P<p1>[A-Z][\w'.\-]*(?: (?:aka |AKA )?[A-Z][\w'.\-]*){0,4})\s+vs?\.?\s+(?P<p2>[A-Z][\w'.\-]*(?: (?:aka |AKA )?[A-Z][\w'.\-]*){0,4})",
)
_SCORE_PATTERN = re.compile(r"(\d{1,3})\s*[-–]\s*(\d{1,3})")

_EXTRACT_PROMPT = """\
You are analysing a 1v1 basketball game video. Extract the following fields as JSON:
{"player1": "...", "player2": "...", "player1_score": <int or null>, "player2_score": <int or null>, "match_date": "YYYY-MM-DD or null"}

Rules:
- player1 and player2 are the two individual players competing.
- Scores are final game scores (integers). Use null if you can't determine them.
- match_date should be in YYYY-MM-DD format. Use null if unknown.
- Return ONLY valid JSON, no commentary.

Video title: {title}

Transcript excerpt (first ~500 words):
{transcript}
"""


def _guess_match_info(title: str, transcript: str | None, published_at: str | None = None) -> dict:
    """Try regex on title, fall back to LLM if needed."""
    info = {"player1": None, "player2": None,
            "player1_score": None, "player2_score": None,
            "match_date": None, "flagged": False}

    # Regex pass on title
    vs_match = _VS_PATTERN.search(title)
    if vs_match:
        info["player1"] = vs_match.group("p1").strip()
        info["player2"] = vs_match.group("p2").strip()

    score_match = _SCORE_PATTERN.search(title)
    if score_match:
        info["player1_score"] = int(score_match.group(1))
        info["player2_score"] = int(score_match.group(2))

    # If we got both players from regex, we're good
    if info["player1"] and info["player2"]:
        return info

    # LLM fallback
    try:
        import ollama as ollama_lib
        excerpt = " ".join((transcript or "").split()[:500])
        resp = ollama_lib.chat(
            model=os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
            messages=[{"role": "user", "content": _EXTRACT_PROMPT.format(
                title=title, transcript=excerpt)}],
            options={"num_ctx": 4096},
        )
        import json as _json
        raw = resp.message.content.strip()
        # Find first { ... } in response
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            data = _json.loads(raw[start:end])
            info["player1"] = data.get("player1") or info["player1"]
            info["player2"] = data.get("player2") or info["player2"]
            if data.get("player1_score") is not None:
                info["player1_score"] = int(data["player1_score"])
            if data.get("player2_score") is not None:
                info["player2_score"] = int(data["player2_score"])
            info["match_date"] = data.get("match_date") or info["match_date"]
    except Exception as exc:
        log.warning("LLM extraction failed: %s", exc)

    # Fallback: use YouTube publish date if match_date is still empty
    if not info["match_date"] and published_at:
        # published_at is typically ISO format like "2024-03-15T12:00:00Z"
        info["match_date"] = published_at[:10]

    # Flag if we still can't identify two players
    if not info["player1"] or not info["player2"]:
        info["flagged"] = True

    return info


@app.get("/add", response_class=HTMLResponse)
async def add_video_page(request: Request):
    return templates.TemplateResponse("discover.html", {"request": request})


@app.post("/api/add/check")
async def add_check(request: Request):
    """Check which video IDs are already in the database."""
    body = await request.json()
    raw_text = body.get("urls", "")
    if isinstance(raw_text, list):
        raw_text = "\n".join(str(u) for u in raw_text)
    video_ids = _extract_video_ids_from_text(raw_text)

    if not video_ids:
        return {"known": [], "unknown": [], "invalid": True}

    known = []
    unknown = []
    for vid in video_ids:
        match_info = get_match_by_video_id(vid)
        if match_info:
            known.append({**match_info, "thumbnail_url": f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"})
        else:
            unknown.append(vid)

    return {"known": known, "unknown": unknown, "invalid": False}


@app.post("/api/add/process")
async def add_process(request: Request):
    """Process unknown videos: fetch metadata, transcript, comments.
    Returns SSE stream with progress and guessed match info."""
    body = await request.json()
    video_ids = body.get("video_ids", [])

    if not video_ids:
        return Response(status_code=400, content="No video IDs provided")

    async def event_stream():
        # Import youtube_ingest functions
        import sys as _sys
        from pathlib import Path as _Path
        yt_ingest_dir = _Path(__file__).parent.parent.parent / "youtube-ingest"
        if str(yt_ingest_dir) not in _sys.path:
            _sys.path.insert(0, str(yt_ingest_dir))

        from youtube_ingest import (
            init_db,
            fetch_video_metadata_batch,
            fetch_transcript,
            clean_transcript,
            fetch_top_comments,
            upsert_video,
            upsert_transcript,
            insert_comments,
            _build_youtube_service,
            set_progress,
            YOUTUBE_API_KEY,
        )

        conn = init_db()
        service = _build_youtube_service() if YOUTUBE_API_KEY else None

        results = []

        for vid in video_ids:
            try:
                yield f"event: progress\ndata: {json.dumps({'video_id': vid, 'status': 'processing', 'message': 'Fetching metadata...'})}\n\n"
                await asyncio.sleep(0)  # yield control

                # 1. Metadata
                meta = {}
                if service:
                    meta_batch = fetch_video_metadata_batch(service, [vid])
                    meta = meta_batch.get(vid, {})

                if not meta:
                    yield f"event: progress\ndata: {json.dumps({'video_id': vid, 'status': 'error', 'message': 'Video not found or private'})}\n\n"
                    continue

                # Store in DB (match_id=None for now, will be set on submit)
                upsert_video(conn, None, vid, meta)

                yield f"event: progress\ndata: {json.dumps({'video_id': vid, 'status': 'processing', 'message': 'Fetching transcript...'})}\n\n"
                await asyncio.sleep(0)

                # 2. Transcript
                raw_text, segments = fetch_transcript(vid)
                cleaned_text = None
                if raw_text:
                    from rag.config import SKIP_OLLAMA
                    if SKIP_OLLAMA:
                        yield f"event: progress\ndata: {json.dumps({'video_id': vid, 'status': 'processing', 'message': 'Storing raw transcript (Ollama cleaning disabled)...'})}\n\n"
                        cleaned_text = raw_text
                    else:
                        yield f"event: progress\ndata: {json.dumps({'video_id': vid, 'status': 'processing', 'message': 'Cleaning transcript with Ollama...'})}\n\n"
                        await asyncio.sleep(0)
                        cleaned_text = await asyncio.to_thread(clean_transcript, raw_text)

                upsert_transcript(conn, vid, raw_text, cleaned_text, segments)

                yield f"event: progress\ndata: {json.dumps({'video_id': vid, 'status': 'processing', 'message': 'Fetching comments...'})}\n\n"
                await asyncio.sleep(0)

                # 3. Comments
                comments = fetch_top_comments(service, vid) if service else []
                insert_comments(conn, vid, comments)

                # 4. Checkpoint
                from datetime import datetime, timezone
                set_progress(conn, f"yt_video:{vid}", datetime.now(timezone.utc).isoformat())

                # 5. Guess match info
                title = meta.get("title", "")
                published_at = meta.get("published_at", "")
                guessed = await asyncio.to_thread(_guess_match_info, title, cleaned_text or raw_text, published_at)

                result = {
                    "video_id": vid,
                    "title": title,
                    "channel": meta.get("channel_name", ""),
                    "view_count": meta.get("view_count", 0),
                    "duration_sec": meta.get("duration_sec", 0),
                    "thumbnail_url": f"https://img.youtube.com/vi/{vid}/hqdefault.jpg",
                    "player1": guessed.get("player1", ""),
                    "player2": guessed.get("player2", ""),
                    "player1_score": guessed.get("player1_score"),
                    "player2_score": guessed.get("player2_score"),
                    "match_date": guessed.get("match_date", ""),
                    "flagged": guessed.get("flagged", False),
                }
                results.append(result)

                yield f"event: progress\ndata: {json.dumps({'video_id': vid, 'status': 'done', 'message': 'Ready for review'})}\n\n"

            except Exception as exc:
                log.exception("Error processing video %s", vid)
                yield f"event: progress\ndata: {json.dumps({'video_id': vid, 'status': 'error', 'message': str(exc)})}\n\n"

        # Final event with all results
        yield f"event: results\ndata: {json.dumps(results)}\n\n"
        yield "event: done\ndata: {}\n\n"
        conn.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/add/submit")
async def add_submit(request: Request):
    """Submit user-corrected match data for a new video."""
    body = await request.json()
    vid = body.get("video_id", "").strip()
    player1 = body.get("player1_name", "").strip()
    player2 = body.get("player2_name", "").strip()
    p1_score = body.get("player1_score")
    p2_score = body.get("player2_score")
    match_date = body.get("match_date", "").strip() or None

    if not vid or not player1 or not player2:
        return Response(status_code=400, content="video_id, player1_name, and player2_name are required")

    # Convert scores to int or None
    try:
        p1_score = int(p1_score) if p1_score not in (None, "", "null") else None
    except (ValueError, TypeError):
        p1_score = None
    try:
        p2_score = int(p2_score) if p2_score not in (None, "", "null") else None
    except (ValueError, TypeError):
        p2_score = None

    # 1. Create match + players + win/loss
    match_row_id = create_match_from_discovery(
        video_id=vid,
        player1_name=player1,
        player2_name=player2,
        player1_score=p1_score,
        player2_score=p2_score,
        match_date=match_date,
    )

    # 2. Write markdown file
    import sys as _sys
    from pathlib import Path as _Path
    yt_ingest_dir = _Path(__file__).parent.parent.parent / "youtube-ingest"
    if str(yt_ingest_dir) not in _sys.path:
        _sys.path.insert(0, str(yt_ingest_dir))

    from youtube_ingest import write_markdown
    from rag.config import DB_PATH
    import sqlite3

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Get metadata + transcript + comments from DB
    meta_row = conn.execute(
        "SELECT title, channel_name, view_count, like_count, duration_sec, published_at "
        "FROM youtube_videos WHERE video_id = ?", (vid,)
    ).fetchone()
    meta = dict(meta_row) if meta_row else {}

    trans_row = conn.execute(
        "SELECT cleaned_text FROM youtube_transcripts WHERE video_id = ?", (vid,)
    ).fetchone()
    cleaned_text = trans_row["cleaned_text"] if trans_row else None

    comment_rows = conn.execute(
        "SELECT comment_id, author, text, like_count, published_at "
        "FROM youtube_comments WHERE video_id = ? ORDER BY like_count DESC LIMIT 20", (vid,)
    ).fetchall()
    comments = [dict(r) for r in comment_rows]
    conn.close()

    match_info = {
        "match_id": f"match-{vid}",
        "player1_name": player1,
        "player2_name": player2,
        "match_date": match_date or "unknown",
    }

    md_path = write_markdown(vid, match_info, meta, cleaned_text, comments)

    # 3. Auto-ingest into ChromaDB
    node_count = 0
    try:
        from rag.ingest import ingest_single_markdown
        node_count = await asyncio.to_thread(ingest_single_markdown, md_path)
    except Exception as exc:
        log.warning("ChromaDB ingest failed for %s: %s", vid, exc)

    return {
        "status": "ok",
        "match_row_id": match_row_id,
        "markdown_path": str(md_path),
        "chroma_nodes": node_count,
        "player1": player1,
        "player2": player2,
    }


@app.post("/api/add/manual")
async def add_manual(request: Request):
    """Create a match record entered manually (no YouTube processing required)."""
    from rag.web.db import create_match_manual

    body = await request.json()
    player1 = body.get("player1_name", "").strip()
    player2 = body.get("player2_name", "").strip()
    youtube_url = body.get("youtube_url", "").strip() or None

    if not player1 or not player2:
        return Response(status_code=400, content="player1_name and player2_name are required")

    p1_score = body.get("player1_score")
    p2_score = body.get("player2_score")
    try:
        p1_score = int(p1_score) if p1_score not in (None, "", "null") else None
    except (ValueError, TypeError):
        p1_score = None
    try:
        p2_score = int(p2_score) if p2_score not in (None, "", "null") else None
    except (ValueError, TypeError):
        p2_score = None

    match_date = body.get("match_date", "").strip() or None
    notes = body.get("notes", "").strip() or None

    match_row_id = create_match_manual(
        player1_name=player1,
        player2_name=player2,
        player1_score=p1_score,
        player2_score=p2_score,
        match_date=match_date,
        youtube_url=youtube_url,
        notes=notes,
    )

    winner = None
    if p1_score is not None and p2_score is not None:
        if p1_score > p2_score:
            winner = player1
        elif p2_score > p1_score:
            winner = player2

    return {
        "status": "ok",
        "match_row_id": match_row_id,
        "player1": player1,
        "player2": player2,
        "winner": winner,
    }


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
