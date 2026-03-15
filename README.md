# HoopRec Scraper

A production-ready web scraping and data ingestion pipeline for [hooprec.com](https://hooprec.com). Extracts player profiles, match results, scores, and YouTube video links into a structured SQLite database and Markdown files — designed to power a RAG (Retrieval-Augmented Generation) knowledge base.

## Features

- **Full-site ingestion** — Scrapes the players directory, matches directory, and every match detail page.
- **Resumeable** — Tracks progress in SQLite; safely re-run after interruptions without re-scraping completed pages.
- **Structured output** — Normalized SQLite schema with players, matches, and a many-to-many join table.
- **Markdown dumps** — Every scraped page is saved as Markdown for vector-store indexing.
- **YouTube extraction** — Automatically finds and stores YouTube video URLs/IDs from match pages.
- **JSON export** — Incrementally writes `matches.json` after each match for downstream consumers.
- **RAG query helpers** — Includes ready-to-use SQL functions (e.g. *"Who has Player A beat that Player B lost to?"*).

## Project Structure

```
hooprec-ingest/
├── hooprec_master_ingest.py   # Main ingestion script
├── schema.sql                 # SQLite schema (players, matches, player_matches)
├── requirements.txt           # Python dependencies
└── data/
    └── hooprec_md/            # Markdown dumps (created at runtime)
```

## Prerequisites

- Python 3.10+
- Chromium (installed via Playwright)

## Getting Started

```bash
cd hooprec-ingest
pip install -r requirements.txt
playwright install chromium
python hooprec_master_ingest.py
```

On first run the script will:

1. Scrape the **players directory** → populate the `players` table + save Markdown.
2. Scrape the **matches directory** → collect all match detail-page links.
3. Fetch each **match detail page** → extract scores, player names, YouTube URLs → update `matches` and `player_matches` tables.
4. Write `matches.json` incrementally after every match.

If the script fails midway, simply re-run it — already-completed matches are skipped automatically.

## Configuration

All settings can be overridden via environment variables or a `.env` file:

| Variable | Default | Description |
|---|---|---|
| `HOOPREC_DB` | `players.db` | Path to the SQLite database file |
| `HOOPREC_MD_DIR` | `data/hooprec_md` | Directory for Markdown page dumps |
| `HOOPREC_JSON` | `matches.json` | Path for the matches JSON export |
| `HOOPREC_DELAY` | `2.5` | Seconds to wait for JS rendering before capture |
| `HOOPREC_CONCUR` | `3` | Max concurrent match detail page fetches |

## Database Schema

The SQLite database contains four tables:

- **`players`** — One row per unique player (name, profile URL, win/loss record).
- **`matches`** — One row per match (players, scores, winner/loser, YouTube link).
- **`player_matches`** — Many-to-many join linking players to matches with result and score.
- **`scrape_progress`** — Checkpoint table enabling resumeable runs.

See [hooprec-ingest/schema.sql](hooprec-ingest/schema.sql) for the full DDL.

## RAG Query Example

The script includes a `query_common_opponents()` helper for answering questions like *"Who has Qel beat that Skoob has lost to?"*:

```python
from hooprec_master_ingest import query_common_opponents
import sqlite3

conn = sqlite3.connect("players.db")
results = query_common_opponents(conn, "Qel", "Skoob")
for r in results:
    print(f"{r['opponent']}  Qel YT: {r['player_a_youtube']}  Skoob YT: {r['player_b_youtube']}")
```

## Tuning

After the first run, inspect the saved Markdown files in `data/hooprec_md/` and compare against the live HTML. You may need to adjust the CSS selectors in `scrape_players_directory()` and `_parse_match_detail()` to match hooprec.com's actual class names and score layout.

## License

Private — not for redistribution.
