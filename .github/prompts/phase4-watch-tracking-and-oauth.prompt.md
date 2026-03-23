---
description: "Phase 4 implementation plan — Watch tracking, embedded player, and YouTube OAuth commenting for RecHoop"
---

# Phase 4: Watch Tracking & OAuth Commenting

## Plan: Persistent Watch History, In-App Video Player, YouTube Comment Integration

**TL;DR**: Add manual watch tracking with dates (persistent in SQLite), an embedded YouTube player that plays videos in-app with fullscreen support, and Google OAuth 2.0 so users can reply to YouTube comments without leaving RecHoop. Single-user local app — no multi-user auth needed yet.

---

## Architecture

- **Watch tracking**: New `watch_history` SQLite table, REST API endpoints, JS-driven badge UI on game cards and source cards
- **Embedded player**: YouTube iframe embed in a modal overlay, auto-marks video as watched on play
- **Google OAuth 2.0**: Popup-based consent flow, token storage in SQLite `google_auth` table, automatic token refresh
- **YouTube Data API v3**: `comments.insert` for replies, `commentThreads.insert` for new top-level comments
- **No new build step**: All vanilla JS, same Tailwind CDN + htmx stack

---

## File Changes

### New tables (auto-created on startup)
```
watch_history
  id           INTEGER PRIMARY KEY AUTOINCREMENT
  video_id     TEXT NOT NULL UNIQUE
  watched_at   TEXT NOT NULL            -- ISO date string
  created_at   TEXT DEFAULT datetime('now')

google_auth
  id            INTEGER PRIMARY KEY CHECK (id = 1)  -- single-user row
  access_token  TEXT
  refresh_token TEXT
  token_expiry  TEXT
  email         TEXT
  updated_at    TEXT DEFAULT datetime('now')
```

### Modified files
- `rag/web/db.py` — Add `ensure_web_tables()`, watch CRUD functions, OAuth token CRUD functions
- `rag/web/app.py` — Add watch API routes, OAuth flow routes, YouTube comment endpoints
- `rag/web/static/app.js` — Add video player, watch badge UI, auth flow, comment reply UI
- `rag/web/templates/base.html` — Add modal + watched badge CSS
- `rag/web/templates/index.html` — Add video player modal container, Google sign-in button in header
- `rag/web/templates/partials/game_cards.html` — Add watch badge, in-app play button, "Mark watched" button
- `rag/web/templates/partials/comments.html` — Add reply button per comment, include `comment_id` for API
- `rag/config.py` — Add `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` env vars
- `rag/requirements.txt` — Add `httpx>=0.27.0`

---

## Steps

### Phase A — Watch Tracking (depends on nothing)

**Step 1**: Add web-app tables to `db.py`
- Add `ensure_web_tables()` function that creates `watch_history` and `google_auth` tables via `CREATE TABLE IF NOT EXISTS`. Call from `app.py` on startup so tables exist before any request.
- Add `mark_watched(video_id, watched_at=None)` — INSERT OR UPDATE with today's date as default.
- Add `unmark_watched(video_id)` — DELETE, return whether a row was removed.
- Add `get_watched()` — Return dict mapping `video_id → watched_at` for all entries (efficient bulk load for UI).
- Add `is_watched(video_id)` — Single-video lookup.

**Step 2**: Add watch API endpoints to `app.py`
- `GET /api/watch` — Return full watched dict (loaded on page init by JS).
- `POST /api/watch/{video_id}` — Mark watched with today's date.
- `DELETE /api/watch/{video_id}` — Remove watched status.
- Call `ensure_web_tables()` inside the `@app.on_event("startup")` handler.

**Step 3**: Add watch UI to frontend
- On page load, JS fetches `GET /api/watch` and stores result in `watchedVideos` dict.
- `applyWatchedBadges()` — Iterate all `[data-video-id]` elements, show/hide green "✓ Watched [date]" badges on thumbnails and toggle button text.
- `toggleWatched(videoId, btn)` — POST or DELETE to API, update local state, re-apply badges.
- Add `data-video-id` attribute to game card container divs.
- Add `.watched-badge` and `.watched-indicator` elements to `game_cards.html`.
- Add "Mark watched" / "✓ Watched" button in each card's action row.
- Apply same watch UI to source cards rendered in `renderSourceCards()`.

### Phase B — Embedded YouTube Player (parallel with Phase A)

**Step 4**: Add video player modal
- Add modal HTML to `index.html`: fixed backdrop, centered 16:9 container, iframe, close button.
- Add CSS to `base.html`: `.video-modal-backdrop`, `.video-modal-content`, `.video-modal-close`.
- `playVideo(videoId)` in JS — Set iframe `src` to `https://www.youtube.com/embed/{videoId}?autoplay=1&rel=0`, show modal.
- `closeVideoModal()` — Hide modal, clear iframe src to stop playback.
- Escape key handler to close modal.
- Auto-mark video as watched when `playVideo()` is called.

**Step 5**: Replace external links with in-app player
- In `game_cards.html`, change thumbnail `<a href>` to `<div onclick="playVideo(...)">`.
- In `renderSourceCards()`, change thumbnail links to use `playVideo()` instead of opening YouTube in new tab.
- Keep YouTube URL available for right-click "open in new tab" if needed.

### Phase C — Google OAuth 2.0 (depends on Step 1 for token storage)

**Step 6**: Add OAuth config
- Add `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` to `rag/config.py`, loaded from env vars.
- Add `httpx>=0.27.0` to `rag/requirements.txt` for async HTTP calls to Google token endpoint.

**Step 7**: Add OAuth endpoints to `app.py`
- `GET /api/auth/status` — Check if tokens exist and are valid, return `{signed_in, email}`.
- `GET /api/auth/login` — Build Google OAuth URL with `youtube.force-ssl` scope, redirect user to consent screen. Use popup flow (not full-page redirect).
- `GET /api/auth/callback` — Exchange authorization code for tokens via `httpx.AsyncClient` POST to `https://oauth2.googleapis.com/token`. Fetch user email from userinfo endpoint. Store all in `google_auth` table. Return HTML that posts message to opener window and closes popup.
- `POST /api/auth/logout` — Clear tokens from `google_auth` table.
- `_get_valid_access_token()` — Internal helper: check expiry, refresh if needed using stored refresh_token, update DB. Return valid access token or None.

**Step 8**: Add auth UI
- Add Google sign-in button to header in `index.html` (next to Clear button).
- `checkAuthStatus()` — Fetch `GET /api/auth/status` on page load, update button text (email or "Sign In") and color.
- `handleAuth()` — If signed in → logout. If not → open OAuth popup via `window.open()`.
- Listen for `postMessage` from OAuth callback popup to trigger `checkAuthStatus()` refresh.

### Phase D — YouTube Commenting (depends on Step 7)

**Step 9**: Add comment API endpoints
- `POST /api/comments/reply` — Accept `{parent_id, text}`. Use `_get_valid_access_token()`, POST to YouTube Data API v3 `comments.insert` with `snippet.parentId` and `snippet.textOriginal`. Return 401 if not signed in.
- `POST /api/comments/post` — Accept `{video_id, text}`. POST to `commentThreads.insert`. Return 401 if not signed in.

**Step 10**: Update comments UI
- Update `get_top_comments()` in `db.py` to include `comment_id` in the SELECT.
- In `comments.html`, add "↩ Reply" button per comment (rendered only if `comment_id` is present).
- `showReplyBox(btn, commentId)` — Create inline input + submit button below the comment. Show "Sign in to reply" hint if not authenticated.
- `submitReply(btn, commentId)` — POST to `/api/comments/reply`, show success/error feedback, auto-remove reply box.
- Support Enter key to submit reply.

### Phase E — Future Roadmap File

**Step 11**: Create `.github/prompts/phase4-future-roadmap.prompt.md`
- Document planned features: recommendation engine from watch history, non-1v1 video expansion, public multi-user deployment, social features.

---

## Relevant Files

### Reuse directly (no modifications)
- `rag/query_engine.py` — Query engines unchanged
- `hooprec-ingest/schema.sql` — Reference for `youtube_comments.comment_id` column

### Modify
- `rag/web/db.py` — New tables + CRUD functions
- `rag/web/app.py` — New API routes (watch, auth, comments)
- `rag/web/static/app.js` — Video player, watch tracking, auth, reply UI
- `rag/web/templates/base.html` — Modal + badge CSS
- `rag/web/templates/index.html` — Modal container, auth button
- `rag/web/templates/partials/game_cards.html` — Watch badges, play-in-app
- `rag/web/templates/partials/comments.html` — Reply buttons
- `rag/config.py` — OAuth env vars
- `rag/requirements.txt` — httpx

---

## Verification

1. **Watch tracking**: Mark a video watched → refresh page → badge persists. Unmark → badge gone.
2. **Embedded player**: Click thumbnail → modal opens with video playing → Escape closes → video stops.
3. **Auto-watch on play**: Play a video → "Mark watched" button updates to "✓ Watched" automatically.
4. **Watch state on source cards**: Ask a question → source cards show watched badges for previously watched videos.
5. **OAuth login**: Click "Sign In" → Google popup → consent → popup closes → button shows email.
6. **OAuth logout**: Click signed-in button → returns to "Sign In" state → tokens cleared.
7. **Comment reply**: Expand comments on a game → click Reply → type text → submit → "Reply posted ✓".
8. **Reply without auth**: Click Reply when not signed in → "Sign in with Google to reply" message appears.
9. **Token refresh**: Wait for token to expire → next API call auto-refreshes → no user action needed.
10. **All 35 existing tests still pass** after changes.

---

## Decisions

- **Single-user watch history**: No user_id column — only one person uses the app locally. Will add user_id foreign key when migrating to multi-user in a future phase.
- **SQLite for token storage**: Tokens persist across restarts. Single `google_auth` row with `CHECK (id = 1)` enforces one user.
- **Popup OAuth flow**: Avoids navigating away from the app. Callback page sends `postMessage` to opener and closes itself.
- **httpx for async HTTP**: Already available in the venv as a transitive dep, now pinned explicitly. Needed for non-blocking token exchange and YouTube API calls.
- **Auto-watch on play**: When user clicks play, they clearly intend to watch. Reduces friction vs requiring a separate "mark watched" click.
- **YouTube iframe embed**: No API key needed for embedding. Uses `?autoplay=1&rel=0` params for clean playback.
- **comment_id from DB**: The `youtube_comments` table already stores `comment_id` (YouTube's ID). Just needed to add it to the SELECT query.
- **No comment posting UI on game cards**: Reply-to-comment only appears in the htmx-loaded comments section. New top-level comment endpoint exists but UI deferred — can add a "Leave a comment" box later.
