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

            CREATE TABLE IF NOT EXISTS player_aliases (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                alias       TEXT NOT NULL,
                player_name TEXT NOT NULL,
                UNIQUE(alias, player_name)
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


# ── Player Aliases ────────────────────────────────────────────

def get_player_aliases() -> dict[str, list[str]]:
    """Return all aliases as {normalized_alias: [player_name, ...]}."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT alias, player_name FROM player_aliases ORDER BY alias"
        ).fetchall()
        result: dict[str, list[str]] = {}
        for r in rows:
            result.setdefault(r["alias"].lower(), []).append(r["player_name"])
        return result
    finally:
        conn.close()


def add_player_alias(alias: str, player_name: str) -> bool:
    """Add an alias for a player. Returns True if inserted, False if duplicate."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO player_aliases (alias, player_name) VALUES (?, ?)",
            (alias.strip().lower(), player_name.strip()),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def remove_player_alias(alias: str, player_name: str) -> bool:
    """Remove an alias. Returns True if deleted."""
    conn = _connect()
    try:
        cur = conn.execute(
            "DELETE FROM player_aliases WHERE alias = ? AND player_name = ?",
            (alias.strip().lower(), player_name.strip()),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_player_stats(player_name: str) -> dict | None:
    """Return overall stats for a single player."""
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT p.name, p.wins, p.losses, p.wins + p.losses AS total_games
            FROM players p
            WHERE p.name = ?
            """,
            (player_name,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_head_to_head(player_a: str, player_b: str) -> dict:
    """Return head-to-head record and game count between two players."""
    conn = _connect()
    try:
        # We need to find how many times A beat B and B beat A
        row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN winner_name = ? THEN 1 ELSE 0 END) AS a_wins,
                SUM(CASE WHEN winner_name = ? THEN 1 ELSE 0 END) AS b_wins,
                COUNT(*) AS total_games
            FROM matches
            WHERE (player1_name = ? AND player2_name = ?)
               OR (player1_name = ? AND player2_name = ?)
            """,
            (player_a, player_b, player_a, player_b, player_b, player_a)
        ).fetchone()
        
        return {
            "player_a": player_a,
            "player_b": player_b,
            "a_wins": row["a_wins"] or 0,
            "b_wins": row["b_wins"] or 0,
            "total_games": row["total_games"] or 0
        }
    finally:
        conn.close()


def get_leaderboard(category: str, limit: int = 10) -> list[dict]:
    """Return a ranked leaderboard.

    Categories: most_wins, best_record, most_games, most_losses, most_viewed.
    """
    conn = _connect()
    try:
        if category == "most_viewed":
            rows = conn.execute(
                """
                SELECT
                    m.player1_name, m.player2_name,
                    yv.title, yv.view_count, yv.channel_name,
                    m.youtube_video_id AS video_id, m.youtube_url, m.match_date
                FROM youtube_videos yv
                JOIN matches m ON m.youtube_video_id = yv.video_id
                ORDER BY yv.view_count DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

        col_map = {
            "most_wins": "wins DESC",
            "most_losses": "losses DESC",
            "most_games": "(wins + losses) DESC",
            "best_record": "CASE WHEN wins + losses >= 3 THEN 1.0 * wins / (wins + losses) ELSE 0 END DESC",
        }
        order = col_map.get(category, "wins DESC")
        min_games = "AND wins + losses >= 3" if category == "best_record" else ""
        rows = conn.execute(
            f"""
            SELECT name, wins, losses, wins + losses AS total_games,
                   ROUND(100.0 * wins / MAX(wins + losses, 1), 1) AS win_pct
            FROM players
            WHERE wins + losses > 0 {min_games}
            ORDER BY {order}
            LIMIT ?
            """,
            (limit,),
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


# ── Video Discovery (Phase 4.1) ──────────────────────────────

def video_exists(video_id: str) -> bool:
    """Return True if a video_id is already in youtube_videos."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM youtube_videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def get_match_by_video_id(video_id: str) -> dict | None:
    """Return match + YouTube metadata for a known video, or None."""
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT
                m.player1_name, m.player2_name,
                m.player1_score, m.player2_score,
                m.winner_name, m.match_date,
                m.youtube_video_id AS video_id,
                yv.title, yv.channel_name, yv.view_count
            FROM matches m
            JOIN youtube_videos yv ON yv.video_id = m.youtube_video_id
            WHERE m.youtube_video_id = ?
            """,
            (video_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_match_from_discovery(
    video_id: str,
    player1_name: str,
    player2_name: str,
    player1_score: int | None,
    player2_score: int | None,
    match_date: str | None,
) -> int:
    """Insert a new match row from a discovered video. Returns the row id.

    Also inserts/updates player rows and player_matches, adjusting
    win/loss counters.
    """
    import re as _re
    from datetime import datetime as _dt

    # Build match_id slug: match-{p1}-vs-{p2}-{m-d-yyyy}
    def _slugify(name: str) -> str:
        return _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    date_part = ""
    if match_date:
        try:
            d = _dt.strptime(match_date, "%Y-%m-%d")
            date_part = f"{d.month}-{d.day}-{d.year}"
        except ValueError:
            date_part = _re.sub(r"[^0-9\-]", "", match_date)

    match_id = f"match-{_slugify(player1_name)}-vs-{_slugify(player2_name)}-{date_part}"

    # Determine winner/loser
    winner_name = None
    loser_name = None
    if player1_score is not None and player2_score is not None:
        if player1_score > player2_score:
            winner_name = player1_name
            loser_name = player2_name
        elif player2_score > player1_score:
            winner_name = player2_name
            loser_name = player1_name

    conn = _connect()
    try:
        # Insert match
        conn.execute(
            """
            INSERT INTO matches
                (match_id, detail_url, player1_name, player2_name,
                 player1_score, player2_score, winner_name, loser_name,
                 youtube_url, youtube_video_id, match_date, scraped_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(match_id) DO UPDATE SET
                player1_score = excluded.player1_score,
                player2_score = excluded.player2_score,
                winner_name   = excluded.winner_name,
                loser_name    = excluded.loser_name,
                match_date    = excluded.match_date
            """,
            (
                match_id,
                f"https://www.youtube.com/watch?v={video_id}",
                player1_name,
                player2_name,
                player1_score,
                player2_score,
                winner_name,
                loser_name,
                f"https://www.youtube.com/watch?v={video_id}",
                video_id,
                match_date,
            ),
        )
        conn.commit()

        match_row_id = conn.execute(
            "SELECT id FROM matches WHERE match_id = ?", (match_id,)
        ).fetchone()["id"]

        # Link youtube_videos.match_id
        conn.execute(
            "UPDATE youtube_videos SET match_id = ? WHERE video_id = ?",
            (match_row_id, video_id),
        )
        conn.commit()

        # Upsert players and player_matches, update win/loss counters
        _link_players(conn, match_row_id, player1_name, player2_name,
                      player1_score, player2_score, winner_name, loser_name)

        return match_row_id
    finally:
        conn.close()


def _link_players(
    conn: sqlite3.Connection,
    match_row_id: int,
    player1_name: str,
    player2_name: str,
    player1_score: int | None,
    player2_score: int | None,
    winner_name: str | None,
    loser_name: str | None,
) -> None:
    """Populate player_matches join table and update win/loss counters."""
    pairs = []
    if player1_name:
        r = "win" if winner_name == player1_name else (
            "loss" if loser_name == player1_name else "unknown")
        pairs.append((player1_name, r, player1_score))
    if player2_name:
        r = "win" if winner_name == player2_name else (
            "loss" if loser_name == player2_name else "unknown")
        pairs.append((player2_name, r, player2_score))

    for name, result, score in pairs:
        row = conn.execute("SELECT id FROM players WHERE name = ?", (name,)).fetchone()
        if not row:
            conn.execute("INSERT OR IGNORE INTO players (name) VALUES (?)", (name,))
            conn.commit()
            row = conn.execute("SELECT id FROM players WHERE name = ?", (name,)).fetchone()
        pid = row["id"]

        conn.execute(
            "INSERT OR REPLACE INTO player_matches (player_id, match_id, result, score) VALUES (?,?,?,?)",
            (pid, match_row_id, result, score),
        )
    conn.commit()

    for name, result, _ in pairs:
        if result == "win":
            conn.execute("UPDATE players SET wins = wins + 1 WHERE name = ?", (name,))
        elif result == "loss":
            conn.execute("UPDATE players SET losses = losses + 1 WHERE name = ?", (name,))
    conn.commit()
