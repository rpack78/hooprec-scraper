# Plan: YouTube Ingest + Local Punctuation Agent

Enrich 598 YouTube-linked matches with video metadata, cleaned transcripts, and top comments. A local Ollama "Punctuation Agent" (llama3.1:8b on RTX 4070 Super) post-processes raw transcripts before storage to improve downstream RAG embedding quality. 100% local, zero API cost. Start with a ~50-match subset, then scale.

---

## Phase 0: Repository Restructure First

1. **Restructure the repository before adding Phase 2 code** — Modify the existing codebase to match the intended long-term structure:
   - Keep `hooprec-ingest/` for Phase 1 ingestion code
   - Add a sibling `youtube-ingest/` directory for Phase 2 code
   - Add a root `data/` directory for persistent project data
   - Keep room for a future `rag/` directory for Phase 3
2. **Move persistent artifacts out of `hooprec-ingest/`** — Update the existing Phase 1 code and scripts so the database, markdown output, JSON exports, and similar long-lived artifacts live under root-level `data/` paths rather than inside `hooprec-ingest/`.
3. **Create empty directories now if needed** — Create the target directories even if some will not be used until later, so the structure is explicit and stable. At minimum:
   - `data/db/`
   - `data/raw/`
   - `data/raw/hooprec_md/`
   - `data/raw/youtube_md/`
   - `youtube-ingest/`
   - `rag/`
4. **Update existing code to match the new paths** — Modify env var defaults, path handling, scripts, and any assumptions in the current ingestion code so Phase 1 still runs correctly after the reorganization.
5. **Update README as part of this first step** — Document the new directory structure, explain what lives in each top-level directory, and update any run instructions or path references affected by the reorganization.
6. **Test that everything still works before Phase 2 work begins** — Run the existing Phase 1 workflow or the most relevant smoke tests to confirm the restructure did not break the current ingestion path.
7. **Pause for user inspection** — After the restructure, directory creation, README updates, and validation are complete, stop and summarize exactly what changed so the user can inspect the new layout before any new YouTube ingestion code is added.

## Phase A: Environment Setup

8. **Ollama** — Downloaded and installed from ollama.com, run `ollama pull llama3.1:8b` (~4.7GB). Verify with `ollama run llama3.1:8b "Hello"` and confirm `localhost:11434` responds.
9. **YouTube API Key** — Store in `.env` as `YOUTUBE_API_KEY`. Free tier gives 10,000 units/day — we need ~1,200 total for all 598 matches. (make sure this .env file is in .gitignore!)
10. **Python dependencies** — Add to the relevant requirements file(s): `youtube-transcript-api>=0.6.0`, `google-api-python-client>=2.0.0`, `ollama>=0.4.0`

## Phase B: Schema & Database

11. **Treat the database as a shared project asset** — Move or rename the DB into a root-level location such as `data/db/hooprec.sqlite` so it is no longer implicitly owned by Phase 1.
12. **Add 3 tables** to `schema.sql` (matching the README's planned schema with one addition):
   - `youtube_videos` — video metadata linked to `matches(id)`
   - `youtube_transcripts` — **two text columns**: `raw_text` (original wall-of-text) + `cleaned_text` (Ollama-processed with punctuation/diarization). Plus `segments` JSON for timestamped chunks.
   - `youtube_comments` — top-level comments with like counts
   - Indexes on `match_id` and `video_id`

## Phase C: Core Ingest Script — New file: `youtube-ingest/youtube_ingest.py`

13. **Script skeleton** — Mirror `hooprec_master_ingest.py` architecture where it makes sense: reuse the checkpoint pattern, env var loading pattern, and logging conventions, but keep the Phase 2 code isolated in `youtube-ingest/`.
14. **Video ID extraction** — Use `youtube_video_id` already stored in `matches` table, fallback to URL parsing for the 3 URL formats
15. **Fetch video metadata** — `videos.list(part="snippet,statistics,contentDetails")` via `google-api-python-client`. Batch up to 50 IDs per API call for efficiency → insert into `youtube_videos`
16. **Fetch transcripts** — `youtube-transcript-api` for auto-generated captions. Store raw segments as JSON, concatenate into `raw_text`. Gracefully handle videos with no captions (NULL, log, continue)
17. **Punctuation Agent** — For each non-NULL transcript, call Ollama `llama3.1:8b` with a prompt like: *"Add punctuation and paragraph breaks to this basketball game transcript. Identify different speakers where possible. Keep all original words."* Store result in `cleaned_text`. **Chunking strategy**: if transcript > 3,000 words, split into ~2,000-word overlapping chunks, process each, reassemble. **Fallback**: if Ollama fails or exceeds 120s timeout, store `raw_text` as `cleaned_text` and log a warning.
18. **Fetch top comments** — `commentThreads.list(order="relevance", maxResults=20)`. Handle comments-disabled gracefully.
19. **Markdown output** — Write one file per video to `data/raw/youtube_md/`. Structure: match header, metadata (views, channel, date), cleaned transcript, top 20 comments. These files feed Phase 3's vector store.
20. **Checkpoint each video** — Key pattern `yt_video:{video_id}` in `scrape_progress`. Safe to interrupt and restart at any point.

## Phase D: CLI Controls

21. **CLI arguments**: `--limit N` (subset size, default all), `--skip-ollama` (test pipeline without GPU), `--dry-run` (preview without processing), `--video-id ID` (single video for debugging)

## Optional Phase E: Whisper Fallback For Missing Captions

22. **When to use it** — Only if `youtube-transcript-api` returns no captions for a video. Keep this out of the initial Phase 2 implementation so the base pipeline stays simple.
23. **Tooling** — Use `faster-whisper`, `yt-dlp`, and `ffmpeg`:
   - `yt-dlp` downloads audio from the YouTube URL
   - `ffmpeg` extracts or converts audio into a clean local file for transcription
   - `faster-whisper` generates timestamped transcript segments locally on GPU
24. **Model choice** — Start with `faster-whisper` model `medium` on the RTX 4070 Super. It is a good balance of speed and accuracy for basketball commentary.
25. **No speaker diarization** — Do not add a diarization tool in this phase. Whisper output stays as plain timestamped transcript text, then the Ollama punctuation step makes it readable.
26. **Fallback flow**:
   - Try `youtube-transcript-api`
   - If captions are missing, download audio with `yt-dlp`
   - Convert or normalize audio with `ffmpeg`
   - Transcribe with `faster-whisper` `medium`
   - Store generated segments in `segments` and concatenated text in `raw_text`
   - Run the same Ollama punctuation pass into `cleaned_text`
27. **Dependencies for the fallback** — Add when this phase is implemented: `faster-whisper`, `yt-dlp`, and ensure `ffmpeg` is installed and available on `PATH`.
28. **Operational tradeoff** — This increases coverage for videos without captions, but it is slower and adds more moving parts. Keep it as an opt-in fallback behind a flag such as `--enable-whisper-fallback`.

---

## Relevant Files

- `hooprec-ingest/hooprec_master_ingest.py` — Reuse `get_progress`/`set_progress`, `init_db`, logging, env var patterns
- `hooprec-ingest/schema.sql` — Add 3 new tables + indexes
- `hooprec-ingest/requirements.txt` — Update if dependencies remain shared with Phase 1, otherwise split Phase 2 dependencies into a dedicated file under `youtube-ingest/`
- **NEW** `youtube-ingest/youtube_ingest.py` — Main Phase 2 script
- **NEW** `data/db/hooprec.sqlite` — Shared database location
- **NEW** `data/raw/hooprec_md/` — Phase 1 markdown output directory
- **NEW** `data/raw/youtube_md/` — Phase 2 transcript Markdown output directory
- **NEW** `rag/` — Placeholder for Phase 3 code

## Verification

1. Restructure validation — confirm the new directories exist, Phase 1 paths resolve correctly, and the current ingestion scripts still work with the new root-level `data/` layout
2. README validation — verify the documented structure and commands match the actual repository after the reorganization
3. Important: Stop here after the restructure/test pass and allow user inspection. Print out what changed, what directories were created, what paths moved, and what the user should inspect before any YouTube ingestion implementation begins.
4. Ollama smoke test — feed a sample transcript snippet, confirm punctuation added and words preserved
5. Single video end-to-end — `python youtube_ingest.py --video-id <id>`, verify all 3 tables populated + Markdown file created, compare `raw_text` vs `cleaned_text`
6. Subset batch — `python youtube_ingest.py --limit 50`, interrupt mid-run and restart to confirm resumeability, spot-check 5 Markdown files
7. Error handling — test with no-caption video, comments-disabled video, and Ollama stopped — each should degrade gracefully
8. Important: Stop here again after the subset run. Print out what tables to inspect, Markdown files, and a full summary of what was done. Do not run the full 598 videos until the user is satisfied with the subset results.
9. Full run — process all 598 after subset validation and user approval

## Decisions

- `raw_text` AND `cleaned_text` stored separately so you can always re-run the Punctuation Agent with a better model/prompt later without re-fetching transcripts
- Markdown files use `cleaned_text` (not raw) since those feed Phase 3 embeddings
- YouTube Data API v3 is technically not "local" but is free-tier and required for metadata/comments — `youtube-transcript-api` needs no API key at all
- Comment replies excluded (only top-level) to keep scope manageable
- The repository should be organized by responsibility: Phase 1 code in `hooprec-ingest/`, Phase 2 code in `youtube-ingest/`, persistent data under root `data/`, and future retrieval/chat code in `rag/`

## Further Considerations

1. **Model choice**: `llama3.1:8b` is the safe default, but `qwen2.5:7b` is also strong at text formatting tasks. You could try both on a few transcripts to compare quality before committing to the full run.
2. **Videos without captions**: Keep the initial Phase 2 path simple and mark them NULL. If coverage becomes a problem, implement the optional Whisper fallback phase above rather than mixing it into the first pass.
