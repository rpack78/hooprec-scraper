# Phase 4.1 — Video Discovery & Manual Ingest

**TL;DR**: Add a "Discover" page (linked from main nav) where users paste YouTube URLs to check if they exist in the database. Known videos show their match info. Unknown videos get fully processed (transcript + comments via Ollama), then present a pre-filled form for you to confirm/correct player names, scores, date, and winner. On submission: create match + player records, update wins/losses, write markdown, and auto-ingest into ChromaDB. Supports multiple URLs at once, presenting forms one at a time.

---

## Phase A: Backend — Lookup & Processing

1. **Add DB lookup helpers to `rag/web/db.py`** — `video_exists(video_id)` and `get_match_by_video_id()` to check `youtube_videos` table and return full match info if found

2. **Add URL parser to `rag/web/app.py`** — `extract_video_ids()` reusing the `_YT_PATTERNS` regex from `hooprec_master_ingest.py` to pull 11-char IDs from raw text, deduplicate

3. **`POST /api/discover/check` route** — Accepts raw textarea text with URLs, parses video IDs, checks DB. Returns `{known: [{video_id, player1, player2, score, date}], unknown: ["id1", "id2"]}`

4. **`POST /api/discover/process` route (SSE)** — For each unknown video_id:
   - Fetch metadata via YouTube Data API (reuse `fetch_video_metadata_batch`)
   - Fetch transcript + clean via Ollama (reuse `fetch_transcript` / `clean_transcript`)
   - Fetch top comments (reuse `fetch_top_comments`)
   - Store in `youtube_videos`, `youtube_transcripts`, `youtube_comments`
   - **Extract player/score guesses**: regex on title first ("Player1 vs Player2", score pattern), LLM fallback if regex fails (send title + 500 words of transcript to Ollama → JSON extraction)
   - If LLM can't identify two players → flag as "possibly not a 1v1 game"
   - Stream SSE progress per video; return guessed form data

5. **`POST /api/discover/submit` route** — Accept user-corrected form data:
   - Create `matches` row (generate slug `match-{p1}-vs-{p2}-{date}`)
   - Create/update `players` rows + `player_matches` + update `wins`/`losses` (mirror `_link_players` logic)
   - Write markdown to `data/raw/youtube_md/yt_{video_id}.md`
   - Auto-ingest single file into ChromaDB (new `ingest_single_markdown()` in `rag/ingest.py`)

---

## Phase B: Frontend — Discover Page

6. **Add nav link** in `index.html` header → "🔍 Discover" linking to `/discover`

7. **`GET /discover` route** → renders new `discover.html` template

8. **Discover page UI** (extends `base.html`, same Tailwind/htmx stack):
   - Textarea for pasting URLs (multiple, one per line)
   - "Check Videos" button
   - **Known videos**: cards showing "Already in database ✓" with match info
   - **Unknown videos**: "Thank you, adding new video. This could take some time." with progress
   - After processing, present pre-filled forms **one at a time**:
     - Fields: Player 1, Player 2, P1 Score, P2 Score, Match Date, Winner (auto-computed)
     - Video thumbnail + title shown for reference
     - If flagged non-1v1: orange warning "⚠ This may not be a 1v1 game"
     - Submit / Skip buttons per form

---

## Phase C: Refactoring for Reuse

9. **Extract shared functions from `youtube-ingest/youtube_ingest.py`** into importable module — `fetch_video_metadata_batch`, `fetch_transcript`, `clean_transcript`, `fetch_top_comments`, `write_markdown`. Keep CLI working.

10. **Extract `_link_players` logic** from `hooprec-ingest/hooprec_master_ingest.py` into a shared utility or duplicate in `rag/web/db.py` for the web app to call directly

11. **Add `ingest_single_markdown(path)` to `rag/ingest.py`** — parse one md file → chunk → embed → store in ChromaDB. Existing batch `run_ingest()` unchanged.

---

## Phase D: Prompt File

12. **Create `.github/prompts/phase4.1-video-discovery.prompt.md`** — Full plan doc following existing prompt format

13. **Update `.github/prompts/phase4-future-roadmap.prompt.md`** — Replace "Recommendations Engine" with "Video Discovery & Manual Ingest", move recommendations later

---

## Relevant Files

| File | Change |
|------|--------|
| `rag/web/app.py` | 3 new API routes (`/api/discover/check`, `/api/discover/process`, `/api/discover/submit`) + `GET /discover` page route |
| `rag/web/db.py` | New lookup + creation functions: `video_exists()`, `get_match_by_video_id()`, `create_match_from_discovery()`, `link_players()` |
| `rag/web/templates/discover.html` | **NEW** — Full discover page with URL input, results area, and pre-filled forms |
| `rag/web/templates/index.html` | Add "🔍 Discover" nav link in header |
| `rag/web/static/app.js` | Discover page JS (URL checking, SSE processing, form management) — or separate `discover.js` |
| `youtube-ingest/youtube_ingest.py` | Refactor to extract reusable functions |
| `rag/ingest.py` | Add `ingest_single_markdown()` |
| `hooprec-ingest/hooprec_master_ingest.py` | Reference `_link_players()` pattern for reuse |
| `.github/prompts/phase4.1-video-discovery.prompt.md` | **NEW** — Phase prompt file |
| `.github/prompts/phase4-future-roadmap.prompt.md` | Update roadmap |

---

## Verification

1. Paste a **known** video URL → "Already in database" with match info
2. Paste an **unknown** URL → processing message → pre-filled form
3. Paste **multiple URLs** (mixed) → known shown immediately, unknown processed sequentially with forms one at a time
4. **Submit form** → verify match in DB, player wins/losses updated, markdown created, ChromaDB count incremented
5. **Ask RAG chat** about the new video → results appear
6. Paste **non-game video** → warning flag shown, user can skip or confirm
7. Paste **garbage text** → validation error, no crash
8. Paste **duplicate URLs** → deduplicated, processed once

---

## Decisions

- **Regex first, LLM fallback** for player/score extraction from video title + transcript
- **Auto-ingest into ChromaDB** on submit (no manual Phase 3 step needed)
- **Non-1v1 flagged but not blocked** — user decides to confirm or skip
- This replaces "Recommendations Engine" as 4.1 in the roadmap (recommendations move later)
- Same `YOUTUBE_API_KEY` env var as Phase 2
- **Inline processing** (not subprocess) for single videos — reuse youtube_ingest functions directly
- Separate page (not modal) per user request
- `match_id` slug convention: `match-{player1_slug}-vs-{player2_slug}-{m-d-yyyy}` to match existing data
