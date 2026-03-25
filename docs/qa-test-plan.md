# RecHoop QA Test Plan

> Reusable manual QA checklist for the RecHoop web app.
> Run against `http://127.0.0.1:8000` after starting with `python -m rag.web`.

---

## Prerequisites

- [ ] Ollama running with `llama3.1:8b` and `nomic-embed-text` models loaded
- [ ] SQLite database at `data/db/hooprec.sqlite` with matches/players data
- [ ] ChromaDB at `data/db/chroma/` with indexed transcripts
- [ ] Server started: `.venv\Scripts\python.exe -m rag.web`
- [ ] Wait for "Ollama warmup complete" in server logs before testing chat

---

## 1. Homepage (`GET /`)

| # | Test | Expected | Status |
|---|------|----------|--------|
| 1.1 | Load homepage | Page renders with RecHoop header, game count, latest games grid | ✅ |
| 1.2 | Game cards display | Each card shows: thumbnail, player names, score, date, view count, channel icon | ✅ |
| 1.3 | Suggested prompts | Four prompt buttons visible (Most exciting, Greatest comeback, Best player, Best trash talk) | ✅ |
| 1.4 | Chat input | Text input + Send button visible at bottom | ✅ |
| 1.5 | Nav bar | Add Video, Refresh Data, Auto/Vector/SQL mode toggles, Clear, Sign In visible | ✅ |
| 1.6 | "VIDEOS YOU MAY LIKE" sidebar | Right sidebar shows video suggestions | ✅ |

---

## 2. Chat — Vector Queries (`POST /api/chat`)

| # | Test | Expected | Status |
|---|------|----------|--------|
| 2.1 | "What are the most exciting games?" | Route shows "⚡ vector", streams narrative response | ✅ |
| 2.2 | "Tell me about Daedae" (player name only) | Route shows "⚡ vector (filtered: Daedae)", returns relevant games | ✅ |
| 2.3 | Source cards display | Cards show thumbnail, player names, YouTube link | ✅ |
| 2.4 | Click suggested prompt | Route shows "⚡ cached" (pre-loaded) or "⚡ vector" | |

---

## 3. Chat — SQL Queries

| # | Test | Expected | Status |
|---|------|----------|--------|
| 3.1 | "Show me all of Daedae's games" | Route shows "⚡ sql (full list)", returns list of games | ✅ (was 🐛) |
| 3.2 | "What is Beno's record?" | Route shows "⚡ sql", returns win/loss stats | ✅ |
| 3.3 | "Who has the most wins?" | Route shows "⚡ sql", returns player rankings | ✅ |
| 3.4 | "How many games has Mike Harden played?" | SQL route, returns game count | |

**Bug found & fixed (3.1):** `httpcore.ReadTimeout` — Ollama cold start + SQL double-LLM-call exceeded 120s timeout.

---

## 4. Chat — Mode Switching

| # | Test | Expected | Status |
|---|------|----------|--------|
| 4.1 | `POST /api/chat/mode/auto` | Returns `{"status":"ok","mode":"auto"}` | ✅ |
| 4.2 | `POST /api/chat/mode/vector` | Returns `{"status":"ok","mode":"vector"}` | ✅ |
| 4.3 | `POST /api/chat/mode/sql` | Returns `{"status":"ok","mode":"sql"}` | ✅ |
| 4.4 | `POST /api/chat/clear` | Returns `{"status":"ok"}`, resets mode to auto | ✅ |

---

## 5. Game Card Actions

| # | Test | Expected | Status |
|---|------|----------|--------|
| 5.1 | `GET /api/games/{video_id}/comments` | Returns top YouTube comments with author, text, likes, reply button | ✅ |
| 5.2 | `GET /api/watch` | Returns watched video IDs (empty `{}` when none) | ✅ |
| 5.3 | `GET /api/games/latest` | Returns 12 game cards as HTML partial | ✅ |
| 5.4 | Click "Ask about this game" on a card | Chat input pre-filled or query sent about that specific game | |
| 5.5 | Click "Mark watched" | Visual indicator changes, saved to DB | |

---

## 6. Add Video Page (`GET /add`)

| # | Test | Expected | Status |
|---|------|----------|--------|
| 6.1 | Load Add Video page | Page renders with URL input area and "Check Videos" button | ✅ |
| 6.2 | Check known video URL | Returns `{"known": [{match details}], "unknown": []}` | ✅ |
| 6.3 | Check unknown video URL | Returns `{"known": [], "unknown": ["VIDEO_ID"]}` | ✅ |
| 6.4 | Check invalid URL | Returns `{"invalid": true}` | ✅ |
| 6.5 | Send URLs as array (API) | Coerced to string, works correctly | ✅ (was 🐛) |
| 6.6 | Submit a new video | Creates match + player records, ingests into ChromaDB | |

**Bug found & fixed (6.5):** `TypeError: expected string or bytes-like object, got 'list'` — endpoint crashed if `urls` was sent as a JSON array instead of string. Added type coercion guard.

---

## 7. Auth (Google OAuth)

| # | Test | Expected | Status |
|---|------|----------|--------|
| 7.1 | Auth status when not signed in | `/api/auth/status` returns `{"signed_in": false}` | ✅ |
| 7.2 | Click "Sign In" without Google credentials | Returns 500 with "GOOGLE_CLIENT_ID not configured" | ⚠️ Expected |

**Note:** Google OAuth requires `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`. Not a bug — configuration dependency.

---

## 8. Refresh Data (`POST /api/ingest/refresh`)

| # | Test | Expected | Status |
|---|------|----------|--------|
| 8.1 | Click "Refresh Data" | Triggers re-ingest of markdown files into ChromaDB | |
| 8.2 | Check game count updates | Header shows updated game count after refresh | |

---

## 9. Error Handling

| # | Test | Expected | Status |
|---|------|----------|--------|
| 9.1 | Send empty chat message | Returns 400 Bad Request, no crash | ✅ |
| 9.2 | Ollama not running | Friendly error message, not raw traceback | |
| 9.3 | LLM timeout | Shows "The AI model took too long..." user-friendly message | ✅ (was 🐛) |
| 9.4 | Warmup on startup | "Ollama warmup complete — model loaded" logged before first query | ✅ |

---

## 10. Channel Icons

| # | Test | Expected | Status |
|---|------|----------|--------|
| 10.1 | Known channel (e.g., Ballislife) | 302 redirect to cached avatar URL | ✅ |
| 10.2 | Known channel (e.g., The Grease Factory) | 200 with avatar image | ✅ |
| 10.3 | Unknown channel | 200 with transparent 1x1 GIF fallback | ✅ |

---

## Bugs Found & Fixed

### BUG-001: SQL Query Timeout on Cold Start
- **Symptom:** "Error: timed out" when asking SQL-routed questions (e.g., "Show me all of Daedae's games")
- **Root Cause:** Ollama model not loaded in VRAM on first query. NLSQLTableQueryEngine makes two sequential LLM calls (text→SQL, SQL results→natural language). Combined time exceeded 120s timeout.
- **Files changed:**
  1. `rag/config.py` — Increased `RAG_LLM_TIMEOUT` default: 120s → 300s
  2. `rag/web/app.py` — Added `_warmup_ollama()` startup task (loads model before first query)
  3. `rag/web/app.py` — User-friendly timeout error message

### BUG-002: Add Video Check Crashes on Array Input
- **Symptom:** 500 Internal Server Error on `POST /api/add/check` when `urls` is a JSON array
- **Root Cause:** `_extract_video_ids_from_text()` expects a string but received a list
- **File changed:** `rag/web/app.py` — Added `isinstance(raw_text, list)` guard to join array into string

---

## Test Environment

- **Date:** 2026-03-23
- **URL:** http://127.0.0.1:8000
- **Python:** 3.13
- **LLM:** llama3.1:8b via Ollama
- **Embeddings:** nomic-embed-text via Ollama
- **DB:** hooprec.sqlite (599 games)
