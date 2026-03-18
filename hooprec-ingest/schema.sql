-- =============================================================
-- Basketball RAG Ingestion Schema
-- =============================================================

-- Players table: one row per unique player found on the directory
CREATE TABLE IF NOT EXISTS players (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    profile_url   TEXT,
    wins          INTEGER DEFAULT 0,
    losses        INTEGER DEFAULT 0,
    scraped_at    TEXT,
    raw_md_path   TEXT
);

-- Matches table: one row per match detail page
CREATE TABLE IF NOT EXISTS matches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id         TEXT    NOT NULL UNIQUE,
    detail_url       TEXT    NOT NULL,
    player1_name     TEXT,
    player2_name     TEXT,
    player1_score    INTEGER,
    player2_score    INTEGER,
    winner_name      TEXT,
    loser_name       TEXT,
    youtube_url      TEXT,
    youtube_video_id TEXT,
    match_date       TEXT,
    scraped_at       TEXT,
    raw_md_path      TEXT
);

-- Many-to-many: links players to the matches they appear in
CREATE TABLE IF NOT EXISTS player_matches (
    player_id   INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    match_id    INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    result      TEXT CHECK(result IN ('win','loss','unknown')),
    score       INTEGER,
    PRIMARY KEY (player_id, match_id)
);

-- Scrape-progress checkpoint table (enables resumeable runs)
CREATE TABLE IF NOT EXISTS scrape_progress (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Indexes optimized for RAG query patterns (e.g. "who has X beat that Y lost to")
CREATE INDEX IF NOT EXISTS idx_matches_winner ON matches(winner_name);
CREATE INDEX IF NOT EXISTS idx_matches_loser  ON matches(loser_name);
CREATE INDEX IF NOT EXISTS idx_pm_player      ON player_matches(player_id);
CREATE INDEX IF NOT EXISTS idx_pm_match       ON player_matches(match_id);

-- =============================================================
-- Phase 2: YouTube Data Tables
-- =============================================================

-- Video metadata linked to a match
CREATE TABLE IF NOT EXISTS youtube_videos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id       INTEGER REFERENCES matches(id),
    video_id       TEXT    NOT NULL UNIQUE,
    title          TEXT,
    description    TEXT,
    channel_name   TEXT,
    view_count     INTEGER,
    like_count     INTEGER,
    comment_count  INTEGER,
    duration_sec   INTEGER,
    published_at   TEXT,
    scraped_at     TEXT
);

-- Transcripts: raw + Ollama-cleaned text, plus timestamped segments
CREATE TABLE IF NOT EXISTS youtube_transcripts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT    NOT NULL UNIQUE REFERENCES youtube_videos(video_id),
    language     TEXT    DEFAULT 'en',
    raw_text     TEXT,
    cleaned_text TEXT,
    segments     TEXT,       -- JSON array of {start, duration, text}
    scraped_at   TEXT
);

-- Top-level comments (replies excluded)
CREATE TABLE IF NOT EXISTS youtube_comments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT    NOT NULL REFERENCES youtube_videos(video_id),
    comment_id   TEXT    NOT NULL UNIQUE,
    author       TEXT,
    text         TEXT,
    like_count   INTEGER,
    published_at TEXT,
    scraped_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_yt_videos_match   ON youtube_videos(match_id);
CREATE INDEX IF NOT EXISTS idx_yt_videos_vid     ON youtube_videos(video_id);
CREATE INDEX IF NOT EXISTS idx_yt_transcripts_vid ON youtube_transcripts(video_id);
CREATE INDEX IF NOT EXISTS idx_yt_comments_vid   ON youtube_comments(video_id);
