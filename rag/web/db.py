"""
db.py — Direct SQLite queries for the web UI.

Provides lightweight read-only helpers for landing page data
(latest games, top comments) without going through the LLM.
Also manages watch history and user preferences.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from rag.config import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_web_tables():
    """Create web-app-specific tables if they don't exist."""
    conn = _connect()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS watch_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id   TEXT NOT NULL UNIQUE,
                watched_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS google_auth (
                id            INTEGER PRIMARY KEY CHECK (id = 1),
                access_token  TEXT,
                refresh_token TEXT,
                token_expiry  TEXT,
                email         TEXT,
                updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        conn.commit()
    finally:
        conn.close()


def _video_id_to_thumbnail(video_id: str | None) -> str:
    """Derive YouTube thumbnail URL from a video ID."""
    if not video_id:
        return ""
    return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"


def get_latest_games(limit: int = 12) -> list[dict]:
    """Return the most recent games with YouTube metadata.

    Joins matches + youtube_videos to get thumbnails, view counts, etc.
    Ordered by match_date descending.
    """
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT
                m.id             AS match_id,
                m.player1_name,
                m.player2_name,
                m.player1_score,
                m.player2_score,
                m.winner_name,
                m.match_date,
                m.youtube_video_id AS video_id,
                m.youtube_url,
                yv.title,
                yv.channel_name,
                yv.view_count,
                yv.like_count,
                yv.duration_sec
            FROM matches m
            LEFT JOIN youtube_videos yv ON yv.video_id = m.youtube_video_id
            WHERE m.youtube_video_id IS NOT NULL
            ORDER BY m.match_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        games = []
        for r in rows:
            games.append(
                {
                    "match_id": r["match_id"],
                    "player1": r["player1_name"],
                    "player2": r["player2_name"],
                    "player1_score": r["player1_score"],
                    "player2_score": r["player2_score"],
                    "winner": r["winner_name"],
                    "match_date": r["match_date"],
                    "video_id": r["video_id"],
                    "youtube_url": r["youtube_url"],
                    "title": r["title"],
                    "channel": r["channel_name"],
                    "view_count": r["view_count"],
                    "like_count": r["like_count"],
                    "duration_sec": r["duration_sec"],
                    "thumbnail_url": _video_id_to_thumbnail(r["video_id"]),
                }
            )
        return games
    finally:
        conn.close()


def get_top_comments(video_id: str, limit: int = 5) -> list[dict]:
    """Return top comments for a video ordered by likes."""
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT comment_id, author, text, like_count, published_at
            FROM youtube_comments
            WHERE video_id = ?
            ORDER BY like_count DESC
            LIMIT ?
            """,
            (video_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_game_count() -> int:
    """Total number of matches with YouTube videos."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM matches WHERE youtube_video_id IS NOT NULL"
        ).fetchone()
        return row["cnt"]
    finally:
        conn.close()


def get_player_games(player_names: list[str], limit: int = 20) -> list[dict]:
    """Return games for the given player(s), newest first, with YouTube metadata."""
    conn = _connect()
    try:
        # Build OR conditions for each player name
        conditions = []
        params = []
        for name in player_names:
            conditions.append("(m.player1_name = ? OR m.player2_name = ?)")
            params.extend([name, name])

        where = " OR ".join(conditions)
        rows = conn.execute(
            f"""
            SELECT
                m.player1_name, m.player2_name,
                m.player1_score, m.player2_score,
                m.winner_name, m.match_date,
                m.youtube_video_id AS video_id,
                m.youtube_url,
                yv.title, yv.channel_name, yv.view_count
            FROM matches m
            LEFT JOIN youtube_videos yv ON yv.video_id = m.youtube_video_id
            WHERE m.youtube_video_id IS NOT NULL AND ({where})
            ORDER BY m.match_date DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Watch History ─────────────────────────────────────────────

def mark_watched(video_id: str, watched_at: str | None = None) -> dict:
    """Mark a video as watched. Returns the watch record."""
    if not watched_at:
        watched_at = date.today().isoformat()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO watch_history (video_id, watched_at)
            VALUES (?, ?)
            ON CONFLICT(video_id) DO UPDATE SET watched_at = excluded.watched_at
            """,
            (video_id, watched_at),
        )
        conn.commit()
        return {"video_id": video_id, "watched_at": watched_at}
    finally:
        conn.close()


def unmark_watched(video_id: str) -> bool:
    """Remove watched status. Returns True if a row was deleted."""
    conn = _connect()
    try:
        cur = conn.execute(
            "DELETE FROM watch_history WHERE video_id = ?", (video_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_watched() -> dict[str, str]:
    """Return dict mapping video_id → watched_at for all watched videos."""
    conn = _connect()
    try:
        rows = conn.execute("SELECT video_id, watched_at FROM watch_history").fetchall()
        return {r["video_id"]: r["watched_at"] for r in rows}
    finally:
        conn.close()


def is_watched(video_id: str) -> str | None:
    """Return watched_at date string if video is watched, else None."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT watched_at FROM watch_history WHERE video_id = ?", (video_id,)
        ).fetchone()
        return row["watched_at"] if row else None
    finally:
        conn.close()


# ── Google OAuth tokens ───────────────────────────────────────

def save_google_tokens(access_token: str, refresh_token: str,
                       token_expiry: str, email: str | None = None):
    """Upsert the single-user Google OAuth token row."""
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO google_auth (id, access_token, refresh_token, token_expiry, email, updated_at)
            VALUES (1, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                access_token  = excluded.access_token,
                refresh_token = excluded.refresh_token,
                token_expiry  = excluded.token_expiry,
                email         = COALESCE(excluded.email, google_auth.email),
                updated_at    = datetime('now')
            """,
            (access_token, refresh_token, token_expiry, email),
        )
        conn.commit()
    finally:
        conn.close()


def get_google_tokens() -> dict | None:
    """Return the stored Google OAuth tokens, or None."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT access_token, refresh_token, token_expiry, email FROM google_auth WHERE id = 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def clear_google_tokens():
    """Delete stored Google OAuth tokens (logout)."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM google_auth WHERE id = 1")
        conn.commit()
    finally:
        conn.close()
