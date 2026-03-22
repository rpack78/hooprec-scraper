## Plan: RAG Test Suite

Create a test suite for the RAG pipeline code. All tests use `tmp_path` fixtures with inline markdown — no Ollama, ChromaDB, or real data needed.

**Run with:**
```bash
cd d:\dev\projects\hooprec-scraper
python -m pytest rag/tests/ -v
```

**Files to create:**
- `rag/tests/__init__.py` — empty
- `rag/tests/test_ingest.py` — 34 unit tests

**Coverage:**

### `parse_youtube_md` (16 tests)
- Extracts player names from H1 heading
- Extracts match date
- Extracts YouTube URL
- Extracts title, channel, duration
- Extracts views/likes as `int` (handles commas)
- Extracts source_file from path
- Splits transcript section correctly
- Splits comments section correctly
- Handles file with no transcript section
- Handles comments when no transcript present
- Handles minimal file (no sections) without crashing
- Views with commas parsed to int

### `_parse_int` (3 tests)
- Plain number
- Number with commas
- Zero

### `build_documents` (8 tests)
- File with transcript + comments → 2 documents
- File without transcript → 1 document (comments only)
- Minimal file (no content) → 0 documents
- Empty directory → empty list
- Ignores non-`yt_` prefixed files
- Metadata propagated to all documents
- `excluded_llm_metadata_keys` and `excluded_embed_metadata_keys` set correctly
- Correct total count across mixed directory (3 files → 3 docs)

### `_filter_new_documents` (3 tests)
- Filters out already-ingested files
- Keeps all documents when nothing ingested yet
- Partial filter: only removes matching files

### `_format_sources` CLI helper (4 tests)
- No `source_nodes` attribute → empty string
- Empty `source_nodes` list → empty string
- Formats single source with match, URL, section, relevance
- Deduplicates by source_file

**Test fixtures use:**
- Nasir Core vs Rob Colon (full file with transcript + comments, Rob wins 31-28, date 6/27/2026)
- Nasir Core vs Beno (comments only, no transcript)
- Player A vs Player B (minimal, no sections)

**Sample fixture:**
```python
SAMPLE_MD_FULL = dedent("""\
    # Nasir Core vs Rob Colon

    **Match date:** 2026-06-27

    **YouTube:** https://www.youtube.com/watch?v=QKhXgjdzvac


    ## Video Metadata

    - **Title:** Rob Colon SHOCKS Nasir Core In An INSTANT CLASSIC | 31-28
    - **Channel:** Ballislife
    - **Views:** 140,881
    - **Likes:** 4,900
    - **Published:** 2026-06-27T23:00:10Z
    - **Duration:** 30m 30s


    ## Transcript

    "I don't care who they put in front of me," said Nas. "I'm built for this."
    Rob with a clutch three to take the lead. 31 to 28 final. Rob Colon wins.


    ## Top Comments (20)

    - **@freezea0** (661 likes): Rob really showed up when it mattered most
    - **@Desjrx2** (442 likes): Yo these refs just there for decoration
""")
```
