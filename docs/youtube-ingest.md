# YouTube Ingest

> Back to [README](../README.md) · See also: [Architecture](architecture.md) · [HoopRec Scraper](hooprec-scraper.md) · [RAG Engine](rag-engine.md)

Enriches the database with the content *inside* match videos. Depends on the [HoopRec Scraper](hooprec-scraper.md) having populated `matches` with YouTube links first.

## What It Does

1. **Video metadata** — Fetches title, description, view count, like count, publish date, channel name, and duration via YouTube Data API v3 (batched, up to 50 IDs per call).
2. **Transcripts** — Extracts auto-generated captions via `youtube-transcript-api`. Raw caption text and timestamped segments are stored separately.
3. **Punctuation agent** — A local Ollama instance (llama3.1:8b) post-processes raw transcripts to add punctuation, capitalization, paragraph breaks, and speaker identification. Long transcripts are chunked into 2,000-word overlapping segments for processing.
4. **Top comments** — Fetches up to 20 top-level comments per video sorted by relevance.
5. **Markdown output** — Generates one file per video in `data/raw/youtube_md/` containing match metadata, video stats, cleaned transcript, and top comments — ready for [RAG Engine](rag-engine.md) vector embedding.

Everything is resumable — checkpoints each video in `scrape_progress`. Raw and cleaned transcripts are stored separately so the punctuation agent can be re-run with a better model/prompt without re-fetching from YouTube.

## Why This Matters

Match stats alone (scores, winner/loser) can't answer questions about *what happened in the game*. The transcript and comments layer is what enables queries like:

- *"Show me a game where someone hit a game-winner at the buzzer."*
- *"What's a controversial call in a Left Hand Dom game?"*
- *"Which games do fans consider the best of all time?"*

## Running It

```bash
cd youtube-ingest
pip install -r requirements.txt
python youtube_ingest.py                    # process all matches
python youtube_ingest.py --limit 50         # first 50 only
python youtube_ingest.py --video-id ABC123  # single video
python youtube_ingest.py --skip-ollama      # skip punctuation pass
python youtube_ingest.py --refresh          # re-fetch metadata + comments only
python youtube_ingest.py --dry-run          # preview, no writes
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `YOUTUBE_API_KEY` | *(required)* | YouTube Data API v3 key (stored in `.env`) |
| `HOOPREC_DB` | `data/db/hooprec.sqlite` | Path to the SQLite database |
| `YOUTUBE_MD_DIR` | `data/raw/youtube_md` | Markdown output directory |
| `OLLAMA_MODEL` | `llama3.1:8b` | Ollama model for transcript cleaning |
| `OLLAMA_TIMEOUT` | `120` | Seconds before Ollama timeout |

## Output

- **SQLite** — `youtube_videos`, `youtube_transcripts`, and `youtube_comments` tables (see [Architecture](architecture.md#database) for row counts)
- **Markdown** — One `.md` file per video in `data/raw/youtube_md/`, used by the [RAG Engine](rag-engine.md) for vector embedding
