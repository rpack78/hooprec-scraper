## Plan: RAG Chat Interface (Phase 3)

Build a hybrid RAG system using LlamaIndex + ChromaDB + Ollama that answers natural-language questions about 1v1 basketball by combining semantic search over YouTube transcripts/comments with structured SQL queries over match stats. CLI-first, fully local.

**Steps**

### Phase A — Ingestion Pipeline

1. **Document loading** — Load ~582 YouTube markdown files from `data/raw/youtube_md/` using LlamaIndex `SimpleDirectoryReader`. Parse metadata from each file header (player names, match date, YouTube URL, view count, likes, channel, duration) and attach it to the Document objects for filtered retrieval later.

2. **Chunking** — Use LlamaIndex `SentenceSplitter` (chunk_size=512, overlap=50 tokens). Chunk transcripts separately from the metadata+comments sections. Keep comments as a single chunk per file. Propagate metadata to every chunk. Estimated total: ~2,000–3,000 chunks across 582 files. *(parallel with step 3 setup)*

3. **Embed & store** — Embed chunks using `OllamaEmbedding(model_name="nomic-embed-text")`, store in ChromaDB with persistent directory at `data/db/chroma/`. Track ingested files so re-runs skip already-processed documents (same resumability pattern as Phases 1 & 2).

   **Output:** `rag/ingest.py` — run once to populate the vector store, re-run to pick up new matches.

### Phase B — Query Engine

4. **Vector query engine** — `VectorStoreIndex` over ChromaDB, using `Ollama` LLM (llama3.1:8b) for response synthesis. Top-k=5 retrieval with optional metadata filtering (e.g., filter by player name when mentioned in query). *(depends on step 3)*

5. **SQL query engine** — `NLSQLTableQueryEngine` over `hooprec.sqlite` exposing `players`, `matches`, `player_matches`, `youtube_videos` tables. Wrap `query_common_opponents()` as a `FunctionTool` so the router can invoke it directly for comparison queries. *(parallel with step 4)*

6. **Hybrid router** — `RouterQueryEngine` with LLM-based routing:
   - Vector engine → narrative/opinion questions (*"show me a controversial game"*)
   - SQL engine → stats/comparison questions (*"who has the best record"*)
   - For complex queries, `SubQuestionQueryEngine` decomposes into sub-parts hitting both engines. *(depends on steps 4 & 5)*

   **Output:** `rag/query_engine.py`

### Phase C — CLI Interface

7. **Interactive REPL** — Prompt loop with response + source citations (chunk snippet, YouTube link). Commands: `/quit`, `/sources` (toggle citation display), `/sql` (force SQL path), `/vector` (force vector path). Pass last N turns as context for follow-up questions. *(depends on step 6)*

   **Output:** `rag/cli.py`

### Phase D — Verification & README

8. **Test the 5 target queries from the README** *(depends on step 7)*:
   - *"What's the most popular 1v1 involving Left Hand Dom?"* → should hit SQL (view_count join)
   - *"Show me a game with a controversial incident"* → should hit vector (transcript + comments)
   - *"Who has Qel beat that Skoob has lost to?"* → should invoke `query_common_opponents()`
   - *"What do fans think of Nasir Core?"* → should hit vector (comments)
   - *"Summarize Left Hand Dom vs Chris Lykes"* → should hit both (hybrid)

9. **Update README** — Mark Phase 3 as active, add setup instructions (Ollama models needed, how to run ingest + CLI).

**File structure**
```
rag/
├── __init__.py
├── ingest.py          # Doc loading, chunking, embedding → ChromaDB
├── query_engine.py    # Vector + SQL engines, hybrid router
├── cli.py             # Interactive CLI REPL
├── config.py          # Paths, model names, chunk sizes (env-configurable)
└── requirements.txt
```

**Relevant files**
- `hooprec-ingest/hooprec_master_ingest.py` — reuse `query_common_opponents()` function (wrap as LlamaIndex `FunctionTool`)
- `hooprec-ingest/schema.sql` — full DB schema for `NLSQLTableQueryEngine`
- `youtube-ingest/youtube_ingest.py` — reference Ollama integration pattern (same library, same local setup)
- `data/raw/youtube_md/` — primary document source (~582 files)
- `data/db/hooprec.sqlite` — SQL query source

**Verification**
1. Run `ingest.py` → confirm ChromaDB collection has ~2,000–3,000 chunks with correct metadata
2. Query ChromaDB directly for "controversial" → verify relevant transcript chunks returned
3. Run each of the 5 target queries through the CLI and verify correct routing (vector vs SQL vs hybrid)
4. Confirm `query_common_opponents("Qel", "Skoob")` returns results with YouTube links through the LlamaIndex tool wrapper
5. Test a follow-up question (e.g., "tell me more about that game") to verify conversation context is maintained

**Decisions**
- Skip `hooprec_md/` files for now — raw HTML scrapes, low signal-to-noise. Can add later if needed.
- Start with llama3.1:8b — upgrade to 70b or newer model only if routing/synthesis quality is poor
- 512-token chunks as baseline — may increase to 1024 if dialogue context gets fragmented during testing
- Web UI deferred to a follow-up phase

**Further Considerations**
1. **Chunk size tuning** — Transcripts with speaker changes may need larger chunks (1024 tokens) to preserve dialogue flow. Start with 512, evaluate after step 8, adjust if answers feel disconnected.
2. **Ollama context window** — llama3.1:8b has 8K context by default. With 5 retrieved chunks + system prompt + conversation history, this may get tight. Monitor for truncation; can increase `num_ctx` to 16K or 32K if needed.
3. **Incremental updates** — When Phase 1/2 scrapers add new matches, `ingest.py` should detect and embed only new files. The plan accounts for this with the resumability tracking in step 3.
