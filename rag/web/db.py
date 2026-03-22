"""
db.py — Direct SQLite queries for the web UI.

Provides lightweight read-only helpers for landing page data
(latest games, top comments) without going through the LLM.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from rag.config import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
            SELECT author, text, like_count, published_at
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
