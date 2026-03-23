# HoopRec Scraper

> Back to [README](../README.md) · See also: [Architecture](architecture.md) · [YouTube Ingest](youtube-ingest.md) · [RAG Engine](rag-engine.md)

The scraper runs daily via Windows Task Scheduler and populates the shared SQLite database with match data from [hooprec.com](https://hooprec.com).

## What It Does

The ingestion script (`hooprec_master_ingest.py`) does three things on each run:

1. **Players** — Calls the HoopRec REST API to fetch all active players (name, ID, profile URL, win/loss record, rating).
2. **Match links** — Scrapes `matches_directory.html` with Playwright, parsing `onclick="window.location.href='match_detail.html?match=...'"` handlers to discover all match slugs.
3. **Match details** — For each unscraped match, fetches the detail page and extracts player names (from `viewPlayer('Name')` onclick), scores (from `<div class="match-score">`), dates (from `<div class="info-value">`), and YouTube URLs.

Everything is resumable — if the script crashes mid-run, re-running it skips already-completed matches.

Match data feeds directly into [YouTube Ingest](youtube-ingest.md), which enriches each match with video metadata and transcripts.

## Running It

```bash
cd hooprec-ingest
pip install -r requirements.txt
playwright install chromium
python hooprec_master_ingest.py
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `HOOPREC_DB` | `data/db/hooprec.sqlite` | Path to the SQLite database |
| `HOOPREC_MD_DIR` | `data/raw/hooprec_md` | Markdown output directory |
| `HOOPREC_JSON` | `data/raw/matches.json` | Matches JSON export path |
| `HOOPREC_DELAY` | `2.5` | Seconds to wait for JS rendering |
| `HOOPREC_CONCUR` | `3` | Max concurrent match-detail fetches |

## Output

- **SQLite** — `players`, `matches`, and `player_matches` tables (see [Architecture](architecture.md#database) for row counts)
- **Markdown** — One `.md` file per match in `data/raw/hooprec_md/`
- **JSON** — `data/raw/matches.json` export of all matches
