"""
youtube_ingest.py
=================
Phase 2: Enrich YouTube-linked matches with video metadata,
cleaned transcripts (via local Ollama), and top comments.

Resumeable — checkpoints each video in `scrape_progress`.
Uses the shared SQLite database at data/db/hooprec.sqlite.

Usage
-----
    python youtube_ingest.py                    # process all matches
    python youtube_ingest.py --limit 50         # first 50 only
    python youtube_ingest.py --video-id ABC123  # single video
    python youtube_ingest.py --skip-ollama      # skip punctuation pass
    python youtube_ingest.py --dry-run          # preview, no writes
    python youtube_ingest.py --refresh          # re-fetch metadata + comments (keeps transcripts)
    python youtube_ingest.py --refresh --limit 10  # refresh a subset

Environment / config (reads .env from project root):
    YOUTUBE_API_KEY   YouTube Data API v3 key (required for metadata/comments)
    HOOPREC_DB        SQLite path  (default: data/db/hooprec.sqlite)
    YOUTUBE_MD_DIR    MD output    (default: data/raw/youtube_md)
    OLLAMA_MODEL      model name   (default: llama3.1:8b)
    OLLAMA_TIMEOUT    seconds      (default: 120)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths & env
# ---------------------------------------------------------------------------
_SCRIPT_DIR   = Path(__file__).parent
_PROJECT_ROOT = _SCRIPT_DIR.parent

load_dotenv(_PROJECT_ROOT / ".env")

DB_PATH    = Path(os.getenv("HOOPREC_DB",    str(_PROJECT_ROOT / "data" / "db" / "hooprec.sqlite")))
MD_DIR     = Path(os.getenv("YOUTUBE_MD_DIR", str(_PROJECT_ROOT / "data" / "raw" / "youtube_md")))
SCHEMA_FILE = _PROJECT_ROOT / "hooprec-ingest" / "schema.sql"

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_TIMEOUT  = int(os.getenv("OLLAMA_TIMEOUT", "120"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("yt-ingest")

# ---------------------------------------------------------------------------
# Database helpers (mirrors Phase 1 patterns)
# ---------------------------------------------------------------------------

def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    if SCHEMA_FILE.exists():
        conn.executescript(SCHEMA_FILE.read_text())
    conn.commit()
    log.info("Database ready: %s", DB_PATH)
    return conn


def get_progress(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM scrape_progress WHERE key = ?", (key,)
    ).fetchone()
    return row[0] if row else None


def set_progress(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO scrape_progress (key, value) VALUES (?,?)",
        (key, value),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Video ID extraction
# ---------------------------------------------------------------------------

_YT_PATTERNS = [
    re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_\-]{11})"),
    re.compile(r"youtube\.com/embed/([A-Za-z0-9_\-]{11})"),
]


def extract_video_id(url: str) -> str | None:
    """Extract 11-char video ID from a YouTube URL."""
    for pat in _YT_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# YouTube Data API helpers
# ---------------------------------------------------------------------------

def _build_youtube_service():
    from googleapiclient.discovery import build
    return build("youtube", "v3", developerKey=YOUTUBE_API_KEY)


def fetch_video_metadata_batch(service, video_ids: list[str]) -> dict[str, dict]:
    """Fetch metadata for up to 50 video IDs in one API call."""
    results: dict[str, dict] = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        resp = service.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(batch),
        ).execute()
        for item in resp.get("items", []):
            vid = item["id"]
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            content = item.get("contentDetails", {})
            results[vid] = {
                "title":         snippet.get("title"),
                "description":   snippet.get("description"),
                "channel_name":  snippet.get("channelTitle"),
                "published_at":  snippet.get("publishedAt"),
                "view_count":    int(stats.get("viewCount", 0)),
                "like_count":    int(stats.get("likeCount", 0)),
                "comment_count": int(stats.get("commentCount", 0)),
                "duration_sec":  _parse_duration(content.get("duration", "")),
            }
    return results


def _parse_duration(iso_dur: str) -> int:
    """Convert ISO 8601 duration (PT1H2M3S) to seconds."""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_dur)
    if not m:
        return 0
    h, mn, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mn * 60 + s


def fetch_top_comments(service, video_id: str, max_results: int = 20) -> list[dict]:
    """Fetch top-level comments sorted by relevance."""
    try:
        resp = service.commentThreads().list(
            part="snippet",
            videoId=video_id,
            order="relevance",
            maxResults=max_results,
            textFormat="plainText",
        ).execute()
    except Exception as exc:
        exc_str = str(exc)
        if "commentsDisabled" in exc_str or "403" in exc_str or "404" in exc_str or "videoNotFound" in exc_str:
            log.info("  Comments unavailable for %s: %s", video_id, type(exc).__name__)
            return []
        raise
    comments = []
    for item in resp.get("items", []):
        snip = item["snippet"]["topLevelComment"]["snippet"]
        comments.append({
            "comment_id":   item["snippet"]["topLevelComment"]["id"],
            "author":       snip.get("authorDisplayName"),
            "text":         snip.get("textDisplay"),
            "like_count":   snip.get("likeCount", 0),
            "published_at": snip.get("publishedAt"),
        })
    return comments


# ---------------------------------------------------------------------------
# Transcript fetching
# ---------------------------------------------------------------------------

def fetch_transcript(video_id: str) -> tuple[str | None, list[dict] | None]:
    """
    Fetch auto-generated or manual captions.
    Returns (raw_text, segments) or (None, None) if unavailable.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt_api = YouTubeTranscriptApi()
        transcript = ytt_api.fetch(video_id)
        segments = [
            {"start": s.start, "duration": s.duration, "text": s.text}
            for s in transcript.snippets
        ]
        raw_text = " ".join(s["text"] for s in segments)
        return raw_text, segments
    except Exception as exc:
        log.info("  No captions for %s: %s", video_id, exc)
        return None, None


# ---------------------------------------------------------------------------
# Ollama punctuation agent
# ---------------------------------------------------------------------------

_PUNCTUATION_PROMPT = textwrap.dedent("""\
    Add punctuation, capitalization, and paragraph breaks to this basketball \
    game transcript. Identify different speakers where possible. \
    Keep ALL original words — do not add, remove, or rephrase any words. \
    Return only the corrected transcript with no commentary.

    TRANSCRIPT:
    {text}
""")

CHUNK_WORD_LIMIT = 3000
CHUNK_SIZE       = 2000
CHUNK_OVERLAP    = 200


def _call_ollama(text: str) -> str | None:
    """Send text to Ollama and return the response, or None on failure."""
    import ollama as ollama_lib
    try:
        resp = ollama_lib.chat(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": _PUNCTUATION_PROMPT.format(text=text)}],
            options={"num_ctx": 8192},
        )
        return resp.message.content
    except Exception as exc:
        log.warning("  Ollama call failed: %s", exc)
        return None


def clean_transcript(raw_text: str) -> str:
    """
    Run the Ollama punctuation agent. Chunks long transcripts.
    Falls back to raw_text if Ollama fails or times out.
    """
    words = raw_text.split()
    if len(words) <= CHUNK_WORD_LIMIT:
        result = _call_ollama(raw_text)
        return result if result else raw_text

    # Chunk with overlap
    chunks: list[str] = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i : i + CHUNK_SIZE])
        chunks.append(chunk)
        i += CHUNK_SIZE - CHUNK_OVERLAP

    cleaned_parts: list[str] = []
    for idx, chunk in enumerate(chunks):
        log.info("    Ollama chunk %d/%d (%d words)", idx + 1, len(chunks), len(chunk.split()))
        result = _call_ollama(chunk)
        cleaned_parts.append(result if result else chunk)

    return "\n\n".join(cleaned_parts)


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------

def write_markdown(
    video_id: str,
    match_info: dict,
    meta: dict,
    cleaned_text: str | None,
    comments: list[dict],
) -> Path:
    MD_DIR.mkdir(parents=True, exist_ok=True)
    safe_slug = re.sub(r"[^\w\-]", "_", match_info.get("match_id", video_id))[:120]
    path = MD_DIR / f"yt_{safe_slug}.md"

    lines: list[str] = []
    p1 = match_info.get("player1_name", "?")
    p2 = match_info.get("player2_name", "?")
    lines.append(f"# {p1} vs {p2}\n")
    lines.append(f"**Match date:** {match_info.get('match_date', 'unknown')}\n")
    lines.append(f"**YouTube:** https://www.youtube.com/watch?v={video_id}\n")

    if meta:
        lines.append(f"\n## Video Metadata\n")
        lines.append(f"- **Title:** {meta.get('title', '')}")
        lines.append(f"- **Channel:** {meta.get('channel_name', '')}")
        lines.append(f"- **Views:** {meta.get('view_count', 0):,}")
        lines.append(f"- **Likes:** {meta.get('like_count', 0):,}")
        lines.append(f"- **Published:** {meta.get('published_at', '')}")
        dur = meta.get("duration_sec", 0)
        lines.append(f"- **Duration:** {dur // 60}m {dur % 60}s\n")

    if cleaned_text:
        lines.append(f"\n## Transcript\n")
        lines.append(cleaned_text)
        lines.append("")

    if comments:
        lines.append(f"\n## Top Comments ({len(comments)})\n")
        for c in comments:
            likes = c.get("like_count", 0)
            author = c.get("author", "")
            text = c.get("text", "").replace("\n", " ")
            lines.append(f"- **{author}** ({likes} likes): {text}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# DB write helpers
# ---------------------------------------------------------------------------

def upsert_video(conn: sqlite3.Connection, match_row_id: int, video_id: str, meta: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO youtube_videos
            (match_id, video_id, title, description, channel_name,
             view_count, like_count, comment_count, duration_sec,
             published_at, scraped_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(video_id) DO UPDATE SET
            title=excluded.title, description=excluded.description,
            channel_name=excluded.channel_name, view_count=excluded.view_count,
            like_count=excluded.like_count, comment_count=excluded.comment_count,
            duration_sec=excluded.duration_sec, published_at=excluded.published_at,
            scraped_at=excluded.scraped_at
        """,
        (
            match_row_id, video_id,
            meta.get("title"), meta.get("description"), meta.get("channel_name"),
            meta.get("view_count"), meta.get("like_count"), meta.get("comment_count"),
            meta.get("duration_sec"), meta.get("published_at"), now,
        ),
    )
    conn.commit()


def upsert_transcript(
    conn: sqlite3.Connection,
    video_id: str,
    raw_text: str | None,
    cleaned_text: str | None,
    segments: list[dict] | None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    seg_json = json.dumps(segments) if segments else None
    conn.execute(
        """
        INSERT INTO youtube_transcripts
            (video_id, raw_text, cleaned_text, segments, scraped_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(video_id) DO UPDATE SET
            raw_text=excluded.raw_text, cleaned_text=excluded.cleaned_text,
            segments=excluded.segments, scraped_at=excluded.scraped_at
        """,
        (video_id, raw_text, cleaned_text, seg_json, now),
    )
    conn.commit()


def insert_comments(conn: sqlite3.Connection, video_id: str, comments: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for c in comments:
        conn.execute(
            """
            INSERT INTO youtube_comments
                (video_id, comment_id, author, text, like_count, published_at, scraped_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(comment_id) DO UPDATE SET
                text=excluded.text, like_count=excluded.like_count,
                scraped_at=excluded.scraped_at
            """,
            (
                video_id, c["comment_id"], c.get("author"),
                c.get("text"), c.get("like_count", 0),
                c.get("published_at"), now,
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def get_matches_with_youtube(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, match_id, player1_name, player2_name,
               youtube_url, youtube_video_id, match_date
        FROM matches
        WHERE youtube_url IS NOT NULL
        ORDER BY id
        """
    ).fetchall()
    cols = ["row_id", "match_id", "player1_name", "player2_name",
            "youtube_url", "youtube_video_id", "match_date"]
    return [dict(zip(cols, r)) for r in rows]


def process_video(
    conn: sqlite3.Connection,
    service,
    match: dict,
    *,
    skip_ollama: bool = False,
    dry_run: bool = False,
) -> None:
    video_id = match.get("youtube_video_id")
    if not video_id:
        video_id = extract_video_id(match.get("youtube_url", ""))
    if not video_id:
        log.warning("  No video ID for match %s — skipping", match["match_id"])
        return

    log.info("Processing %s  (%s vs %s)", video_id,
             match.get("player1_name", "?"), match.get("player2_name", "?"))

    if dry_run:
        log.info("  [dry-run] Would process video %s", video_id)
        return

    # 1. Metadata
    meta_batch = fetch_video_metadata_batch(service, [video_id])
    meta = meta_batch.get(video_id, {})
    if not meta:
        log.warning("  No metadata returned for %s (deleted/private?)", video_id)
        meta = {}

    upsert_video(conn, match["row_id"], video_id, meta)

    # 2. Transcript
    raw_text, segments = fetch_transcript(video_id)
    cleaned_text = None
    if raw_text and not skip_ollama:
        log.info("  Running Ollama punctuation (%d words) ...", len(raw_text.split()))
        cleaned_text = clean_transcript(raw_text)
    elif raw_text:
        cleaned_text = raw_text  # --skip-ollama: store raw as cleaned
    upsert_transcript(conn, video_id, raw_text, cleaned_text, segments)

    # 3. Comments
    comments = fetch_top_comments(service, video_id) if YOUTUBE_API_KEY else []
    insert_comments(conn, video_id, comments)

    # 4. Markdown
    md_path = write_markdown(video_id, match, meta, cleaned_text, comments)
    log.info("  Markdown: %s", md_path)

    # 5. Checkpoint
    set_progress(conn, f"yt_video:{video_id}", datetime.now(timezone.utc).isoformat())


def refresh_video(
    conn: sqlite3.Connection,
    service,
    match: dict,
    *,
    dry_run: bool = False,
) -> None:
    """Re-fetch metadata + comments for an already-ingested video.
    Keeps the existing transcript/cleaned_text untouched.
    Regenerates the markdown file with fresh stats."""
    video_id = match.get("youtube_video_id")
    if not video_id:
        video_id = extract_video_id(match.get("youtube_url", ""))
    if not video_id:
        return

    # Only refresh videos we've already processed
    if not get_progress(conn, f"yt_video:{video_id}"):
        return

    log.info("Refreshing %s  (%s vs %s)", video_id,
             match.get("player1_name", "?"), match.get("player2_name", "?"))

    if dry_run:
        log.info("  [dry-run] Would refresh video %s", video_id)
        return

    # 1. Metadata
    meta_batch = fetch_video_metadata_batch(service, [video_id])
    meta = meta_batch.get(video_id, {})
    if meta:
        upsert_video(conn, match["row_id"], video_id, meta)
    else:
        log.warning("  No metadata returned for %s — keeping existing", video_id)
        row = conn.execute(
            "SELECT title, description, channel_name, view_count, like_count, "
            "comment_count, duration_sec, published_at FROM youtube_videos WHERE video_id=?",
            (video_id,),
        ).fetchone()
        if row:
            meta = dict(zip(["title","description","channel_name","view_count",
                            "like_count","comment_count","duration_sec","published_at"], row))

    # 2. Comments
    comments = fetch_top_comments(service, video_id) if YOUTUBE_API_KEY else []
    if comments:
        insert_comments(conn, video_id, comments)

    # 3. Regenerate markdown with existing transcript
    t_row = conn.execute(
        "SELECT cleaned_text FROM youtube_transcripts WHERE video_id=?", (video_id,)
    ).fetchone()
    cleaned_text = t_row[0] if t_row else None

    if not comments:
        c_rows = conn.execute(
            "SELECT comment_id, author, text, like_count, published_at "
            "FROM youtube_comments WHERE video_id=? ORDER BY like_count DESC LIMIT 20",
            (video_id,),
        ).fetchall()
        comments = [dict(zip(["comment_id","author","text","like_count","published_at"], r)) for r in c_rows]

    md_path = write_markdown(video_id, match, meta, cleaned_text, comments)
    log.info("  Markdown refreshed: %s", md_path)


def _get_skip_ollama() -> bool:
    """Read the global SKIP_OLLAMA flag from the shared config."""
    try:
        sys.path.insert(0, str(_PROJECT_ROOT / "rag"))
        from config import SKIP_OLLAMA
        return SKIP_OLLAMA
    except Exception:
        return os.getenv("SKIP_OLLAMA", "false").lower() in ("1", "true", "yes")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="YouTube ingest for HoopRec matches",
        epilog="Default (no flags): ingest new videos + refresh existing.",
    )
    parser.add_argument("--no-refresh",  action="store_true",  help="Ingest only new videos, skip refreshing existing")
    parser.add_argument("--limit",       type=int, default=0,  help="Process at most N videos (0 = all)")
    parser.add_argument("--dry-run",     action="store_true",  help="Preview without writing anything")
    args = parser.parse_args()

    if not YOUTUBE_API_KEY:
        log.error("YOUTUBE_API_KEY not set. Create a .env file in the project root.")
        sys.exit(1)

    skip_ollama = _get_skip_ollama()
    if skip_ollama:
        log.info("SKIP_OLLAMA=true — transcript cleaning disabled")

    conn = init_db()
    service = _build_youtube_service()

    matches = get_matches_with_youtube(conn)
    total = len(matches)
    log.info("Found %d matches with YouTube links", total)

    # --- Phase 1: Ingest new (unprocessed) videos ---
    new_pending = []
    for m in matches:
        vid = m.get("youtube_video_id") or extract_video_id(m.get("youtube_url", "") or "")
        if vid and get_progress(conn, f"yt_video:{vid}"):
            continue
        new_pending.append(m)
    log.info("New videos to ingest: %d  |  Already done: %d", len(new_pending), total - len(new_pending))

    if args.limit and new_pending:
        new_pending = new_pending[: args.limit]
        log.info("Limited new videos to %d", len(new_pending))

    for i, m in enumerate(new_pending, 1):
        log.info("=== NEW [%d / %d] ===", i, len(new_pending))
        try:
            process_video(conn, service, m, skip_ollama=skip_ollama, dry_run=args.dry_run)
        except Exception as exc:
            log.error("Failed on match %s: %s", m["match_id"], exc, exc_info=True)
            continue

    # --- Phase 2: Refresh existing videos (unless --no-refresh) ---
    if not args.no_refresh:
        refresh_pending = []
        for m in matches:
            vid = m.get("youtube_video_id") or extract_video_id(m.get("youtube_url", "") or "")
            if vid and get_progress(conn, f"yt_video:{vid}"):
                refresh_pending.append(m)
        log.info("Videos to refresh: %d", len(refresh_pending))

        if args.limit and refresh_pending:
            refresh_pending = refresh_pending[: args.limit]
            log.info("Limited refresh to %d", len(refresh_pending))

        for i, m in enumerate(refresh_pending, 1):
            log.info("=== REFRESH [%d / %d] ===", i, len(refresh_pending))
            try:
                refresh_video(conn, service, m, dry_run=args.dry_run)
            except Exception as exc:
                log.error("Failed on match %s: %s", m["match_id"], exc, exc_info=True)
                continue

    # Summary
    yt_vids   = conn.execute("SELECT COUNT(*) FROM youtube_videos").fetchone()[0]
    yt_trans  = conn.execute("SELECT COUNT(*) FROM youtube_transcripts WHERE raw_text IS NOT NULL").fetchone()[0]
    yt_clean  = conn.execute("SELECT COUNT(*) FROM youtube_transcripts WHERE cleaned_text IS NOT NULL").fetchone()[0]
    yt_comms  = conn.execute("SELECT COUNT(*) FROM youtube_comments").fetchone()[0]

    log.info("=" * 50)
    log.info("YouTube ingest summary:")
    log.info("  Videos with metadata : %d", yt_vids)
    log.info("  Transcripts (raw)    : %d", yt_trans)
    log.info("  Transcripts (cleaned): %d", yt_clean)
    log.info("  Comments             : %d", yt_comms)
    log.info("  Markdown dir         : %s", MD_DIR)
    log.info("=" * 50)
    conn.close()


if __name__ == "__main__":
    main()
