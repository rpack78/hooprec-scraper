/**
 * discover.js — Client-side logic for the Video Discovery page.
 *
 * Handles URL checking, SSE processing progress, and review form workflow.
 */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let pendingResults = [];   // results from processing, awaiting review
let currentFormIndex = 0;  // which form we're currently showing
let submittedCount = 0;
let skippedCount = 0;

// ---------------------------------------------------------------------------
// Check Videos
// ---------------------------------------------------------------------------

function checkVideos() {
    const textarea = document.getElementById('url-input');
    const btn = document.getElementById('check-btn');
    const status = document.getElementById('check-status');
    const raw = textarea.value.trim();

    if (!raw) {
        status.textContent = 'Please paste at least one YouTube URL.';
        status.className = 'text-xs text-red-400';
        return;
    }

    btn.disabled = true;
    status.textContent = 'Checking...';
    status.className = 'text-xs text-gray-400';

    fetch('/api/discover/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ urls: raw }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.invalid) {
                status.textContent = 'No valid YouTube URLs found. Check your input.';
                status.className = 'text-xs text-red-400';
                btn.disabled = false;
                return;
            }

            // Show results section
            document.getElementById('results-section').classList.remove('hidden');

            // Render known videos
            if (data.known.length > 0) {
                renderKnownVideos(data.known);
            }

            // Process unknown videos
            if (data.unknown.length > 0) {
                processUnknownVideos(data.unknown);
            } else {
                status.textContent = `All ${data.known.length} video(s) already in database.`;
                status.className = 'text-xs text-green-400';
                btn.disabled = false;
            }
        })
        .catch(err => {
            status.textContent = 'Error checking videos: ' + err.message;
            status.className = 'text-xs text-red-400';
            btn.disabled = false;
        });
}

// ---------------------------------------------------------------------------
// Render Known Videos
// ---------------------------------------------------------------------------

function renderKnownVideos(known) {
    const section = document.getElementById('known-section');
    const container = document.getElementById('known-cards');
    section.classList.remove('hidden');
    container.innerHTML = '';

    for (const v of known) {
        const card = document.createElement('div');
        card.className = 'bg-hoop-card border border-green-800/30 rounded-xl p-4 flex gap-4 fade-in';
        card.innerHTML = `
            <img src="${escapeAttr(v.thumbnail_url || '')}" alt="" class="w-32 h-20 object-cover rounded-lg flex-none" />
            <div class="min-w-0">
                <div class="flex items-center gap-2 mb-1">
                    <span class="text-xs bg-green-900/40 text-green-400 px-2 py-0.5 rounded-full font-medium">✓ In Database</span>
                </div>
                <h4 class="text-white font-medium text-sm truncate">${escapeHtml(v.title || `${v.player1_name} vs ${v.player2_name}`)}</h4>
                <p class="text-gray-400 text-xs mt-1">
                    ${escapeHtml(v.player1_name || '?')} vs ${escapeHtml(v.player2_name || '?')}
                    ${v.player1_score != null ? `&nbsp;·&nbsp;${v.player1_score} - ${v.player2_score}` : ''}
                    ${v.match_date ? `&nbsp;·&nbsp;${v.match_date}` : ''}
                </p>
            </div>
        `;
        container.appendChild(card);
    }
}

// ---------------------------------------------------------------------------
// Process Unknown Videos (SSE)
// ---------------------------------------------------------------------------

function processUnknownVideos(videoIds) {
    const section = document.getElementById('processing-section');
    const container = document.getElementById('processing-cards');
    section.classList.remove('hidden');
    container.innerHTML = '';

    // Create progress cards for each
    for (const vid of videoIds) {
        const card = document.createElement('div');
        card.id = `proc-${vid}`;
        card.className = 'bg-hoop-card border border-hoop-border rounded-xl p-4 flex items-center gap-4';
        card.innerHTML = `
            <img src="https://img.youtube.com/vi/${escapeAttr(vid)}/hqdefault.jpg" alt="" class="w-24 h-14 object-cover rounded-lg flex-none" />
            <div class="min-w-0 flex-1">
                <p class="text-white text-sm font-medium">${escapeHtml(vid)}</p>
                <p class="text-gray-400 text-xs mt-1 proc-status">⏳ Waiting...</p>
            </div>
            <div class="proc-spinner flex-none">
                <svg class="animate-spin h-5 w-5 text-hoop-orange" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
                </svg>
            </div>
        `;
        container.appendChild(card);
    }

    // Start SSE
    fetch('/api/discover/process', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_ids: videoIds }),
    }).then(response => {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let eventType = '';

        function read() {
            reader.read().then(({ done, value }) => {
                if (done) return;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();

                for (const line of lines) {
                    if (line.startsWith('event: ')) {
                        eventType = line.slice(7).trim();
                    } else if (line.startsWith('data: ')) {
                        const data = JSON.parse(line.slice(6));
                        handleProcessEvent(eventType, data);
                    }
                }
                read();
            });
        }
        read();
    });
}

function handleProcessEvent(eventType, data) {
    if (eventType === 'progress') {
        const card = document.getElementById(`proc-${data.video_id}`);
        if (!card) return;
        const statusEl = card.querySelector('.proc-status');
        const spinner = card.querySelector('.proc-spinner');

        if (data.status === 'processing') {
            statusEl.textContent = `🔄 ${data.message}`;
            statusEl.className = 'text-hoop-orange text-xs mt-1 proc-status';
        } else if (data.status === 'done') {
            statusEl.textContent = '✅ Ready for review';
            statusEl.className = 'text-green-400 text-xs mt-1 proc-status';
            spinner.innerHTML = '<span class="text-green-400 text-lg">✓</span>';
        } else if (data.status === 'error') {
            statusEl.textContent = `❌ ${data.message}`;
            statusEl.className = 'text-red-400 text-xs mt-1 proc-status';
            spinner.innerHTML = '<span class="text-red-400 text-lg">✕</span>';
        }
    } else if (eventType === 'results') {
        pendingResults = data;
        if (pendingResults.length > 0) {
            showReviewForms();
        } else {
            showDone();
        }
    } else if (eventType === 'done') {
        document.getElementById('check-btn').disabled = false;
        document.getElementById('check-status').textContent = '';
        // If no results came, show done
        if (pendingResults.length === 0) {
            showDone();
        }
    }
}

// ---------------------------------------------------------------------------
// Review Forms (one at a time)
// ---------------------------------------------------------------------------

function showReviewForms() {
    document.getElementById('review-section').classList.remove('hidden');
    currentFormIndex = 0;
    renderCurrentForm();
}

function renderCurrentForm() {
    const container = document.getElementById('review-form-container');
    const progress = document.getElementById('review-progress');

    if (currentFormIndex >= pendingResults.length) {
        showDone();
        return;
    }

    const v = pendingResults[currentFormIndex];
    const total = pendingResults.length;
    progress.textContent = `Video ${currentFormIndex + 1} of ${total}`;

    const flagWarning = v.flagged
        ? `<div class="bg-orange-900/30 border border-orange-700/40 rounded-lg p-3 mb-4 flex items-start gap-2">
               <span class="text-orange-400 text-lg">⚠</span>
               <div>
                   <p class="text-orange-300 text-sm font-medium">This may not be a 1v1 game</p>
                   <p class="text-orange-400/70 text-xs">Could not confidently identify two players. Please verify or skip.</p>
               </div>
           </div>`
        : '';

    container.innerHTML = `
        <div class="bg-hoop-card border border-hoop-border rounded-xl p-6 fade-in">
            ${flagWarning}
            <!-- Video reference -->
            <div class="flex gap-4 mb-6">
                <img src="${escapeAttr(v.thumbnail_url)}" alt="" class="w-40 h-24 object-cover rounded-lg flex-none" />
                <div class="min-w-0">
                    <h4 class="text-white font-medium text-sm">${escapeHtml(v.title)}</h4>
                    <p class="text-gray-400 text-xs mt-1">${escapeHtml(v.channel)} · ${(v.view_count || 0).toLocaleString()} views · ${formatDuration(v.duration_sec)}</p>
                </div>
            </div>

            <!-- Form fields -->
            <div class="grid grid-cols-2 gap-4 mb-4">
                <div>
                    <label class="block text-xs text-gray-400 mb-1">Player 1</label>
                    <input type="text" id="form-player1" value="${escapeAttr(v.player1 || '')}"
                           class="w-full bg-hoop-darker border border-hoop-border rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-hoop-orange/50" />
                </div>
                <div>
                    <label class="block text-xs text-gray-400 mb-1">Player 2</label>
                    <input type="text" id="form-player2" value="${escapeAttr(v.player2 || '')}"
                           class="w-full bg-hoop-darker border border-hoop-border rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-hoop-orange/50" />
                </div>
                <div>
                    <label class="block text-xs text-gray-400 mb-1">Player 1 Score</label>
                    <input type="number" id="form-p1score" value="${v.player1_score != null ? v.player1_score : ''}"
                           class="w-full bg-hoop-darker border border-hoop-border rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-hoop-orange/50" />
                </div>
                <div>
                    <label class="block text-xs text-gray-400 mb-1">Player 2 Score</label>
                    <input type="number" id="form-p2score" value="${v.player2_score != null ? v.player2_score : ''}"
                           class="w-full bg-hoop-darker border border-hoop-border rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-hoop-orange/50" />
                </div>
                <div>
                    <label class="block text-xs text-gray-400 mb-1">Match Date</label>
                    <input type="date" id="form-date" value="${escapeAttr(v.match_date || '')}"
                           class="w-full bg-hoop-darker border border-hoop-border rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-hoop-orange/50" />
                </div>
                <div>
                    <label class="block text-xs text-gray-400 mb-1">Winner</label>
                    <p id="form-winner" class="text-sm text-gray-300 py-2">${computeWinnerDisplay(v)}</p>
                </div>
            </div>

            <!-- Actions -->
            <div class="flex items-center justify-between pt-4 border-t border-hoop-border">
                <button onclick="skipForm()" class="text-sm text-gray-400 hover:text-white transition-colors px-4 py-2">
                    Skip →
                </button>
                <div class="flex gap-2">
                    <span id="submit-status" class="text-xs text-gray-500 self-center"></span>
                    <button onclick="submitForm('${escapeAttr(v.video_id)}')" id="submit-btn"
                            class="bg-hoop-orange hover:bg-hoop-amber text-white font-medium rounded-xl px-6 py-2.5 text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
                        ✓ Submit Match
                    </button>
                </div>
            </div>
        </div>
    `;

    // Auto-compute winner when scores change
    const p1Input = document.getElementById('form-p1score');
    const p2Input = document.getElementById('form-p2score');
    p1Input.addEventListener('input', updateWinnerDisplay);
    p2Input.addEventListener('input', updateWinnerDisplay);
}

function computeWinnerDisplay(v) {
    const p1 = v.player1 || 'Player 1';
    const p2 = v.player2 || 'Player 2';
    const s1 = v.player1_score;
    const s2 = v.player2_score;
    if (s1 != null && s2 != null) {
        if (s1 > s2) return `<span class="text-green-400 font-medium">${escapeHtml(p1)}</span>`;
        if (s2 > s1) return `<span class="text-green-400 font-medium">${escapeHtml(p2)}</span>`;
        return '<span class="text-gray-500">Tie</span>';
    }
    return '<span class="text-gray-500">—</span>';
}

function updateWinnerDisplay() {
    const p1 = document.getElementById('form-player1').value || 'Player 1';
    const p2 = document.getElementById('form-player2').value || 'Player 2';
    const s1 = parseInt(document.getElementById('form-p1score').value);
    const s2 = parseInt(document.getElementById('form-p2score').value);
    const el = document.getElementById('form-winner');

    if (!isNaN(s1) && !isNaN(s2)) {
        if (s1 > s2) el.innerHTML = `<span class="text-green-400 font-medium">${escapeHtml(p1)}</span>`;
        else if (s2 > s1) el.innerHTML = `<span class="text-green-400 font-medium">${escapeHtml(p2)}</span>`;
        else el.innerHTML = '<span class="text-gray-500">Tie</span>';
    } else {
        el.innerHTML = '<span class="text-gray-500">—</span>';
    }
}

function skipForm() {
    skippedCount++;
    currentFormIndex++;
    renderCurrentForm();
}

function submitForm(videoId) {
    const btn = document.getElementById('submit-btn');
    const status = document.getElementById('submit-status');
    const player1 = document.getElementById('form-player1').value.trim();
    const player2 = document.getElementById('form-player2').value.trim();

    if (!player1 || !player2) {
        status.textContent = 'Player names required';
        status.className = 'text-xs text-red-400 self-center';
        return;
    }

    btn.disabled = true;
    status.textContent = 'Submitting...';
    status.className = 'text-xs text-hoop-orange self-center';

    const payload = {
        video_id: videoId,
        player1_name: player1,
        player2_name: player2,
        player1_score: document.getElementById('form-p1score').value || null,
        player2_score: document.getElementById('form-p2score').value || null,
        match_date: document.getElementById('form-date').value || null,
    };

    fetch('/api/discover/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then(r => {
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return r.json();
        })
        .then(data => {
            submittedCount++;
            status.textContent = `✓ Added: ${data.player1} vs ${data.player2}`;
            status.className = 'text-xs text-green-400 self-center';

            // Move to next form after brief delay
            setTimeout(() => {
                currentFormIndex++;
                renderCurrentForm();
            }, 1000);
        })
        .catch(err => {
            status.textContent = 'Error: ' + err.message;
            status.className = 'text-xs text-red-400 self-center';
            btn.disabled = false;
        });
}

// ---------------------------------------------------------------------------
// Done / Reset
// ---------------------------------------------------------------------------

function showDone() {
    document.getElementById('review-section').classList.add('hidden');
    document.getElementById('processing-section').classList.add('hidden');
    const section = document.getElementById('done-section');
    section.classList.remove('hidden');

    const parts = [];
    if (submittedCount > 0) parts.push(`${submittedCount} new match${submittedCount > 1 ? 'es' : ''} added`);
    if (skippedCount > 0) parts.push(`${skippedCount} skipped`);
    const knownCount = document.getElementById('known-cards').children.length;
    if (knownCount > 0) parts.push(`${knownCount} already in database`);

    document.getElementById('done-summary').textContent = parts.join(' · ') || 'No videos to process.';
}

function resetDiscover() {
    // Reset state
    pendingResults = [];
    currentFormIndex = 0;
    submittedCount = 0;
    skippedCount = 0;

    // Reset UI
    document.getElementById('url-input').value = '';
    document.getElementById('check-btn').disabled = false;
    document.getElementById('check-status').textContent = '';
    document.getElementById('results-section').classList.add('hidden');
    document.getElementById('known-section').classList.add('hidden');
    document.getElementById('processing-section').classList.add('hidden');
    document.getElementById('review-section').classList.add('hidden');
    document.getElementById('done-section').classList.add('hidden');
    document.getElementById('known-cards').innerHTML = '';
    document.getElementById('processing-cards').innerHTML = '';
    document.getElementById('review-form-container').innerHTML = '';
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function escapeHtml(str) {
    if (!str) return '';
    const el = document.createElement('span');
    el.textContent = str;
    return el.innerHTML;
}

function escapeAttr(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function formatDuration(sec) {
    if (!sec) return '';
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}m ${s}s`;
}
