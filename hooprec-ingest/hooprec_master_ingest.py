"""
hooprec_master_ingest.py
========================
Production-ready, resumeable ingestion script for a 1v1 basketball stats site.

Architecture
------------
1. Scrape players_directory.html  -> populate `players` table + Markdown files
2. Scrape matches_directory.html  -> collect all detail-page links
3. For each unscraped match       -> fetch detail page, extract YouTube URL +
                                     scores, update `matches` + `player_matches`
4. Dump matches metadata          -> matches.json (append-friendly)

Resumeability
-------------
Every successfully-scraped match ID is stored in `scrape_progress`.
On restart the script skips already-processed IDs.

Usage
-----
    python hooprec_master_ingest.py

Environment / config overrides (optional .env or env vars):
    HOOPREC_DB      path to SQLite file   (default: players.db)
    HOOPREC_MD_DIR  Markdown output dir   (default: data/hooprec_md)
    HOOPREC_JSON    matches JSON path      (default: matches.json)
    HOOPREC_DELAY   JS settle delay secs  (default: 2.5)
    HOOPREC_CONCUR  concurrent detail fetches (default: 3)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
import warnings

import aiofiles
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_URL        = "https://hooprec.com"
PLAYERS_DIR_URL = f"{BASE_URL}/players_directory.html"
MATCHES_DIR_URL = f"{BASE_URL}/matches_directory.html"

_SCRIPT_DIR = Path(__file__).parent

DB_PATH     = Path(os.getenv("HOOPREC_DB",     str(_SCRIPT_DIR / "players.db")))
MD_DIR      = Path(os.getenv("HOOPREC_MD_DIR", str(_SCRIPT_DIR / "data" / "hooprec_md")))
JSON_PATH   = Path(os.getenv("HOOPREC_JSON",   str(_SCRIPT_DIR / "matches.json")))
JS_DELAY    = float(os.getenv("HOOPREC_DELAY", "2.5"))
CONCURRENCY = int(os.getenv("HOOPREC_CONCUR",  "3"))

SCHEMA_FILE = Path(__file__).parent / "schema.sql"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ingest")

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def init_db() -> sqlite3.Connection:
    """Create tables from schema.sql if they don't already exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    if SCHEMA_FILE.exists():
        conn.executescript(SCHEMA_FILE.read_text())
    else:
        conn.executescript(_INLINE_SCHEMA)
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


def already_scraped_matches(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT match_id FROM matches WHERE scraped_at IS NOT NULL"
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# crawl4ai shared config
# ---------------------------------------------------------------------------

BROWSER_CFG = BrowserConfig(
    headless=True,
    verbose=False,
    extra_args=["--disable-gpu", "--no-sandbox"],
)

def run_cfg(wait_for: str | None = None, delay: float = JS_DELAY) -> CrawlerRunConfig:
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        delay_before_return_html=delay,
        wait_for=wait_for,
        page_timeout=60_000,
    )


# ---------------------------------------------------------------------------
# Markdown persistence
# ---------------------------------------------------------------------------

async def save_markdown(slug: str, content: str) -> Path:
    MD_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-]", "_", slug)[:120]
    path = MD_DIR / f"{safe}.md"
    async with aiofiles.open(path, "w", encoding="utf-8") as fh:
        await fh.write(content)
    return path


# ---------------------------------------------------------------------------
# matches.json helpers
# ---------------------------------------------------------------------------

def load_matches_json() -> list[dict]:
    if JSON_PATH.exists():
        return json.loads(JSON_PATH.read_text(encoding="utf-8"))
    return []


def save_matches_json(records: list[dict]) -> None:
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Step 1 - Players directory
# ---------------------------------------------------------------------------

_PLAYERS_API_URL = (
    "https://v1-basketball-api-1053404524627.us-central1.run.app/api/players"
)
_PLAYERS_API_HEADERS = {"Origin": BASE_URL, "Referer": f"{BASE_URL}/"}


async def scrape_players_directory(
    crawler: AsyncWebCrawler,  # kept for call-site compatibility; not used
    conn: sqlite3.Connection,
) -> list[dict]:
    """Fetch all players via the site's REST API (no browser required).

    The API endpoint supports a limit parameter; limit=500 returns all ~204
    active players in one request.
    """
    log.info("Fetching players from API ...")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            resp = requests.get(
                _PLAYERS_API_URL,
                params={"limit": 500},
                headers=_PLAYERS_API_HEADERS,
                timeout=30,
            )
    except requests.RequestException as exc:
        log.error("Players API request failed: %s", exc)
        log.error("Existing player data is untouched — skipping player refresh.")
        return []

    if resp.status_code in (401, 403):
        log.error("Players API returned HTTP %d — access denied.", resp.status_code)
        log.error(
            "Well... the day we've been dreading finally came. "
            "They locked us out. It was a fun ride while it lasted."
        )
        log.error("Existing player data is safe — nothing was modified.")
        return []

    if resp.status_code != 200:
        log.error("Players API returned HTTP %d", resp.status_code)
        log.error("Existing player data is untouched — skipping player refresh.")
        return []

    try:
        api_data = resp.json()
    except (ValueError, requests.exceptions.JSONDecodeError):
        log.error("Players API returned non-JSON response — possible lockdown or CDN change.")
        log.error("Existing player data is untouched — skipping player refresh.")
        return []

    if not isinstance(api_data, list) or not api_data:
        log.error("Unexpected players API response: %s", type(api_data))
        log.error("Existing player data is untouched — skipping player refresh.")
        return []

    log.info("Players API: %d players received", len(api_data))

    # Build a simple markdown summary and save it
    md_lines = ["# Players Directory\n"]
    for p in api_data:
        md_lines.append(
            f"## {p['name']}\n\n"
            f"- Wins: {p.get('wins', 0)}  "
            f"Losses: {p.get('losses', 0)}  "
            f"Total: {p.get('totalGames', 0)}\n"
            f"- Rating: {p.get('rating', '')}  "
            f"Location: {p.get('location', '')}\n"
        )
    md_path = await save_markdown("players_directory", "\n".join(md_lines))

    now = datetime.now(timezone.utc).isoformat()
    players_out: list[dict] = []
    for p in api_data:
        name = p["name"]
        profile_url = f"{BASE_URL}/player_profile.html?player={p['id']}"
        conn.execute(
            """
            INSERT INTO players (name, profile_url, scraped_at, raw_md_path)
            VALUES (:name, :profile_url, :ts, :md)
            ON CONFLICT(name) DO UPDATE SET
                profile_url = excluded.profile_url,
                scraped_at  = excluded.scraped_at,
                raw_md_path = excluded.raw_md_path
            """,
            {"name": name, "profile_url": profile_url, "ts": now, "md": str(md_path)},
        )
        players_out.append({"name": name, "profile_url": profile_url})
    conn.commit()

    set_progress(conn, "players_directory", now)
    return players_out


# ---------------------------------------------------------------------------
# Step 2 - Matches directory
# ---------------------------------------------------------------------------

async def scrape_matches_directory(
    crawler: AsyncWebCrawler,
    conn: sqlite3.Connection,
) -> list[dict]:
    """Collect all match detail links. Returns list of {match_id, detail_url}.

    Match cards use onclick handlers (window.location.href='match_detail.html?match=...')
    rather than <a> tags, so we parse the raw HTML for onclick attributes.
    """
    log.info("Scraping matches directory ...")

    # Wait until at least one match card's onclick is present in the DOM
    wait_condition = (
        "() => document.querySelectorAll('[onclick*=match_detail]').length > 0"
    )

    result = await crawler.arun(url=MATCHES_DIR_URL, config=run_cfg(wait_for=wait_condition))

    if not result.success:
        log.error("Failed to fetch matches directory: %s", result.error_message)
        return []

    await save_markdown("matches_directory", result.markdown or "")

    matches: list[dict] = []
    seen: set[str] = set()

    # Match cards use: onclick="window.location.href='match_detail.html?match=SLUG'"
    _onclick_re = re.compile(r"window\.location\.href=['\"]([^'\"]*match_detail[^'\"]*)")

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(result.html or "", "html.parser")
    for tag in soup.find_all(attrs={"onclick": _onclick_re}):
        onclick_val = tag.get("onclick", "")
        m = _onclick_re.search(onclick_val)
        if not m:
            continue
        rel_href = m.group(1)
        full_url = rel_href if rel_href.startswith("http") else urljoin(BASE_URL, rel_href)
        parsed = urlparse(full_url)
        qs = parse_qs(parsed.query)
        match_id = qs.get("match", [None])[0]
        if not match_id:
            match_id = parsed.path.split("=")[-1]
        if match_id and match_id not in seen:
            seen.add(match_id)
            matches.append({"match_id": match_id, "detail_url": full_url})

    log.info("Found %d match detail links", len(matches))
    set_progress(conn, "matches_directory_count", str(len(matches)))
    return matches


# ---------------------------------------------------------------------------
# Step 3 - Match detail pages
# ---------------------------------------------------------------------------

_YT_PATTERNS = [
    re.compile(r'(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_\-]{11})'),
    re.compile(r'youtube\.com/embed/([A-Za-z0-9_\-]{11})'),
]

def _extract_youtube(text: str) -> tuple[str | None, str | None]:
    for pat in _YT_PATTERNS:
        m = pat.search(text)
        if m:
            vid = m.group(1)
            return f"https://www.youtube.com/watch?v={vid}", vid
    return None, None


# Player name pattern on match detail pages: onclick="viewPlayer('Name')"
_VIEW_PLAYER_RE = re.compile(r"viewPlayer\(['\"](.+?)['\"]\)")
# Player card pattern on players directory: onclick="viewPlayer(123, 'Name')"
_VP_DIR_RE = re.compile(r"viewPlayer\((\d+),\s*['\"](.+?)['\"]\)")
# Score element: <div class="match-score">30 - 28</div>
_MATCH_SCORE_RE = re.compile(r'(\d+)\s*[-–]\s*(\d+)')
# Date element: <div class="info-value">2/15/2026</div>
_DATE_RE = re.compile(r'\d{1,2}/\d{1,2}/\d{4}')


def _parse_match_detail(result, match_id: str, detail_url: str) -> dict:
    """
    Extract structured data from a match detail page.

    Player names come from onclick="viewPlayer('Name')" divs.
    Score comes from <div class="match-score">P1 - P2</div>.
    """
    from bs4 import BeautifulSoup

    html     = result.html or ""
    markdown = result.markdown or ""

    youtube_url, youtube_vid = _extract_youtube(html + "\n" + markdown)

    soup = BeautifulSoup(html, "html.parser")

    # --- Scores: prefer the dedicated .match-score div ---
    p1_score = p2_score = None
    score_div = soup.find(class_="match-score")
    if score_div:
        m = _MATCH_SCORE_RE.search(score_div.get_text())
        if m:
            p1_score, p2_score = int(m.group(1)), int(m.group(2))
    if p1_score is None:
        # Fallback: first bare "N - M" in the page (avoid "Record: 0-0" etc.)
        for tag in soup.find_all(string=_MATCH_SCORE_RE):
            parent_text = (tag.parent.get_text(strip=True) if tag.parent else "")
            if "record" not in parent_text.lower() and "score:" not in parent_text.lower():
                m = _MATCH_SCORE_RE.search(str(tag))
                if m:
                    p1_score, p2_score = int(m.group(1)), int(m.group(2))
                    break

    # --- Player names: onclick="viewPlayer('Name')" ---
    player_names: list[str] = []
    seen_names: set[str] = set()
    for tag in soup.find_all(attrs={"onclick": _VIEW_PLAYER_RE}):
        m = _VIEW_PLAYER_RE.search(tag.get("onclick", ""))
        if m:
            name = m.group(1).strip()
            if name and name not in seen_names:
                seen_names.add(name)
                player_names.append(name)

    p1 = player_names[0] if len(player_names) > 0 else None
    p2 = player_names[1] if len(player_names) > 1 else None

    winner = loser = None
    if p1_score is not None and p2_score is not None and p1 and p2:
        if p1_score > p2_score:
            winner, loser = p1, p2
        elif p2_score > p1_score:
            winner, loser = p2, p1

    # --- Date: <div class="info-value">2/15/2026</div> ---
    match_date = None
    for tag in soup.find_all(class_="info-value"):
        text = tag.get_text(strip=True)
        if _DATE_RE.fullmatch(text):
            match_date = text
            break

    return {
        "match_id":         match_id,
        "detail_url":       detail_url,
        "player1_name":     p1,
        "player2_name":     p2,
        "player1_score":    p1_score,
        "player2_score":    p2_score,
        "winner_name":      winner,
        "loser_name":       loser,
        "youtube_url":      youtube_url,
        "youtube_video_id": youtube_vid,
        "match_date":       match_date,
        "scraped_at":       datetime.now(timezone.utc).isoformat(),
    }


def _upsert_match(conn: sqlite3.Connection, rec: dict) -> int:
    conn.execute(
        """
        INSERT INTO matches (
            match_id, detail_url,
            player1_name, player2_name,
            player1_score, player2_score,
            winner_name, loser_name,
            youtube_url, youtube_video_id,
            match_date, scraped_at, raw_md_path
        ) VALUES (
            :match_id, :detail_url,
            :player1_name, :player2_name,
            :player1_score, :player2_score,
            :winner_name, :loser_name,
            :youtube_url, :youtube_video_id,
            :match_date, :scraped_at, :raw_md_path
        )
        ON CONFLICT(match_id) DO UPDATE SET
            player1_name     = excluded.player1_name,
            player2_name     = excluded.player2_name,
            player1_score    = excluded.player1_score,
            player2_score    = excluded.player2_score,
            winner_name      = excluded.winner_name,
            loser_name       = excluded.loser_name,
            youtube_url      = excluded.youtube_url,
            youtube_video_id = excluded.youtube_video_id,
            match_date       = excluded.match_date,
            scraped_at       = excluded.scraped_at,
            raw_md_path      = excluded.raw_md_path
        """,
        rec,
    )
    conn.commit()
    row = conn.execute("SELECT id FROM matches WHERE match_id = ?", (rec["match_id"],)).fetchone()
    return row[0]


def _link_players(conn: sqlite3.Connection, match_row_id: int, rec: dict) -> None:
    """Populate player_matches join table and update win/loss counters."""
    pairs = []
    if rec["player1_name"]:
        r = "win" if rec["winner_name"] == rec["player1_name"] else (
            "loss" if rec["loser_name"] == rec["player1_name"] else "unknown")
        pairs.append((rec["player1_name"], r, rec["player1_score"]))
    if rec["player2_name"]:
        r = "win" if rec["winner_name"] == rec["player2_name"] else (
            "loss" if rec["loser_name"] == rec["player2_name"] else "unknown")
        pairs.append((rec["player2_name"], r, rec["player2_score"]))

    for name, result, score in pairs:
        player_row = conn.execute("SELECT id FROM players WHERE name = ?", (name,)).fetchone()
        if not player_row:
            conn.execute("INSERT OR IGNORE INTO players (name) VALUES (?)", (name,))
            conn.commit()
            player_row = conn.execute("SELECT id FROM players WHERE name = ?", (name,)).fetchone()
        pid = player_row[0]

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


async def scrape_match_detail(
    crawler: AsyncWebCrawler,
    conn: sqlite3.Connection,
    match: dict,
    json_records: list[dict],
) -> None:
    match_id   = match["match_id"]
    detail_url = match["detail_url"]

    result = await crawler.arun(url=detail_url, config=run_cfg(delay=JS_DELAY))

    if not result.success:
        log.warning("Match %s – fetch failed: %s", match_id, result.error_message)
        return

    md_path = await save_markdown(f"match_{match_id}", result.markdown or "")
    rec = _parse_match_detail(result, match_id, detail_url)
    rec["raw_md_path"] = str(md_path)

    match_row_id = _upsert_match(conn, rec)
    _link_players(conn, match_row_id, rec)

    json_records.append(rec)
    log.info(
        "Match %-8s  %s vs %s  (%s-%s)  YT: %s",
        match_id,
        rec["player1_name"] or "?",
        rec["player2_name"] or "?",
        rec["player1_score"],
        rec["player2_score"],
        "✓" if rec["youtube_url"] else "✗",
    )


# ---------------------------------------------------------------------------
# Concurrency wrapper
# ---------------------------------------------------------------------------

async def process_matches(
    crawler: AsyncWebCrawler,
    conn: sqlite3.Connection,
    matches: list[dict],
    done: set[str],
) -> None:
    pending = [m for m in matches if m["match_id"] not in done]
    log.info("Matches to scrape: %d  (already done: %d)", len(pending), len(done))

    json_records = load_matches_json()
    sem = asyncio.Semaphore(CONCURRENCY)

    async def bounded(match: dict) -> None:
        async with sem:
            await scrape_match_detail(crawler, conn, match, json_records)
            save_matches_json(json_records)  # write after every match

    tasks = [asyncio.create_task(bounded(m)) for m in pending]
    for i, task in enumerate(asyncio.as_completed(tasks), 1):
        await task
        if i % 10 == 0:
            log.info("Progress: %d / %d matches processed", i, len(pending))


# ---------------------------------------------------------------------------
# RAG query helpers — importable by your orchestrator
# ---------------------------------------------------------------------------

def query_common_opponents(conn: sqlite3.Connection, player_a: str, player_b: str) -> list[dict]:
    """
    Answer: "Who has <player_a> beat that <player_b> has lost to?"

    Returns list of dicts: opponent name + YouTube links for both games.
    Example: query_common_opponents(conn, "Qel", "Skoob")
    """
    sql = """
    SELECT
        opp.name           AS opponent,
        ma.match_id        AS player_a_match_id,
        mb.match_id        AS player_b_match_id,
        ma.youtube_url     AS player_a_youtube,
        mb.youtube_url     AS player_b_youtube
    FROM players pa
    JOIN player_matches pma ON pma.player_id = pa.id AND pma.result = 'win'
    JOIN matches ma         ON ma.id = pma.match_id
    JOIN players opp ON (
        (ma.player1_name = opp.name AND ma.player2_name = pa.name) OR
        (ma.player2_name = opp.name AND ma.player1_name = pa.name)
    )
    JOIN players pb         ON pb.name = :player_b
    JOIN player_matches pmb ON pmb.player_id = pb.id AND pmb.result = 'loss'
    JOIN matches mb         ON mb.id = pmb.match_id AND (
        mb.player1_name = opp.name OR mb.player2_name = opp.name
    )
    WHERE pa.name = :player_a
    ORDER BY opp.name
    """
    rows = conn.execute(sql, {"player_a": player_a, "player_b": player_b}).fetchall()
    cols = ["opponent", "player_a_match_id", "player_b_match_id", "player_a_youtube", "player_b_youtube"]
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Inline fallback schema
# ---------------------------------------------------------------------------
_INLINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
    profile_url TEXT, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
    scraped_at TEXT, raw_md_path TEXT
);
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT NOT NULL UNIQUE,
    detail_url TEXT NOT NULL, player1_name TEXT, player2_name TEXT,
    player1_score INTEGER, player2_score INTEGER,
    winner_name TEXT, loser_name TEXT,
    youtube_url TEXT, youtube_video_id TEXT,
    match_date TEXT, scraped_at TEXT, raw_md_path TEXT
);
CREATE TABLE IF NOT EXISTS player_matches (
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    match_id  INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    result TEXT CHECK(result IN ('win','loss','unknown')),
    score  INTEGER,
    PRIMARY KEY (player_id, match_id)
);
CREATE TABLE IF NOT EXISTS scrape_progress (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_matches_winner ON matches(winner_name);
CREATE INDEX IF NOT EXISTS idx_matches_loser  ON matches(loser_name);
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    conn = init_db()

    async with AsyncWebCrawler(config=BROWSER_CFG) as crawler:
        # Step 1: Players
        # Detect stale data from old broken scraper (which captured the nav
        # link "Players" instead of actual player cards).
        bad_row = conn.execute(
            "SELECT 1 FROM players WHERE name = 'Players'"
        ).fetchone()
        if bad_row:
            log.info("Detected stale player data from old scraper — cleaning up.")
            conn.execute("DELETE FROM players WHERE name = 'Players'")
            conn.execute("DELETE FROM scrape_progress WHERE key = 'players_directory'")
            conn.commit()

        if get_progress(conn, "players_directory"):
            log.info("Players directory already scraped. Skipping.")
        else:
            await scrape_players_directory(crawler, conn)

        # Step 2: Match links
        matches = await scrape_matches_directory(crawler, conn)
        if not matches:
            log.error("No matches found — aborting.")
            return

        # Step 3: Detail pages (resumeable)
        done_ids = already_scraped_matches(conn)
        await process_matches(crawler, conn, matches, done_ids)

    total_players = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    total_matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    with_yt       = conn.execute("SELECT COUNT(*) FROM matches WHERE youtube_url IS NOT NULL").fetchone()[0]

    log.info("=" * 50)
    log.info("Ingestion complete.")
    log.info("  Players : %d", total_players)
    log.info("  Matches : %d  (%d with YouTube)", total_matches, with_yt)
    log.info("  DB      : %s", DB_PATH)
    log.info("  JSON    : %s", JSON_PATH)
    log.info("  Markdown: %s/", MD_DIR)
    log.info("=" * 50)

    # Demo RAG query
    demo = query_common_opponents(conn, "Qel", "Skoob")
    if demo:
        log.info("Demo — opponents Qel beat that Skoob lost to:")
        for row in demo:
            log.info("  %s | Qel: %s | Skoob: %s",
                     row["opponent"], row["player_a_youtube"], row["player_b_youtube"])
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
