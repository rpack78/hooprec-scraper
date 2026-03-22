/**
 * app.js — Client-side logic for RecHoop Chat.
 *
 * Handles SSE streaming, chat UI state transitions, mode switching,
 * and source card rendering.
 */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let isStreaming = false;
let chatStarted = false;
let streamingRawText = '';  // accumulates raw text during streaming

// ---------------------------------------------------------------------------
// Chat submission
// ---------------------------------------------------------------------------

function handleSubmit(e) {
    e.preventDefault();
    const input = document.getElementById('chat-input');
    const message = input.value.trim();
    if (!message || isStreaming) return;
    input.value = '';
    sendMessage(message);
}

function sendPrompt(text) {
    if (isStreaming) return;
    document.getElementById('chat-input').value = '';
    sendMessage(text);
}

function askAboutGame(player1, player2) {
    if (isStreaming) return;
    sendMessage(`Tell me about the game between ${player1} and ${player2}`);
}

// ---------------------------------------------------------------------------
// SSE streaming
// ---------------------------------------------------------------------------

function sendMessage(message) {
    if (isStreaming) return;
    isStreaming = true;

    // Transition from landing to chat view
    if (!chatStarted) {
        chatStarted = true;
        document.getElementById('landing').classList.add('hidden');
        document.getElementById('chat-messages').classList.remove('hidden');
        document.getElementById('source-panel').classList.remove('hidden');
    }

    // Disable input
    const input = document.getElementById('chat-input');
    const btn = document.getElementById('send-btn');
    input.disabled = true;
    btn.disabled = true;

    // Add user message bubble
    appendMessage('user', message);

    // Create assistant bubble (will stream into)
    streamingRawText = '';
    const assistantBubble = appendMessage('assistant', '');
    const contentEl = assistantBubble.querySelector('.msg-content');
    contentEl.classList.add('typing-cursor');

    // Clear previous sources with loading state
    const sourceCards = document.getElementById('source-cards');
    sourceCards.innerHTML = `
        <div class="shimmer h-32 rounded-lg"></div>
        <div class="shimmer h-32 rounded-lg"></div>
    `;

    // POST to /api/chat, read SSE stream
    fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message }),
    }).then(response => {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        function read() {
            reader.read().then(({ done, value }) => {
                if (done) {
                    finishStream(contentEl, input, btn);
                    return;
                }

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // keep incomplete line in buffer

                let eventType = '';
                for (const line of lines) {
                    if (line.startsWith('event: ')) {
                        eventType = line.slice(7).trim();
                    } else if (line.startsWith('data: ')) {
                        const data = line.slice(6);
                        handleSSEEvent(eventType, data, contentEl, sourceCards);
                    }
                }
                read();
            });
        }
        read();
    }).catch(err => {
        contentEl.textContent = `Error: ${err.message}`;
        finishStream(contentEl, input, btn);
    });
}

function handleSSEEvent(eventType, data, contentEl, sourceCards) {
    switch (eventType) {
        case 'token':
            try {
                const token = JSON.parse(data);
                streamingRawText += token;
                contentEl.innerHTML = renderMarkdown(streamingRawText);
                scrollToBottom();
            } catch (e) { /* ignore parse errors */ }
            break;

        case 'route':
            try {
                const note = JSON.parse(data);
                streamingRawText += `{{route:${note}}}`;
            } catch (e) { /* ignore */ }
            break;

        case 'sources':
            try {
                const cards = JSON.parse(data);
                renderSourceCards(cards, sourceCards);
            } catch (e) { /* ignore parse errors */ }
            break;

        case 'done':
            // Handled by stream end
            break;

        case 'error':
            try {
                const errMsg = JSON.parse(data);
                streamingRawText += `\n\n⚠️ Error: ${errMsg}`;
                contentEl.innerHTML = renderMarkdown(streamingRawText);
            } catch (e) { /* ignore */ }
            break;
    }
}

function finishStream(contentEl, input, btn) {
    contentEl.classList.remove('typing-cursor');
    // Final markdown render pass
    if (streamingRawText) {
        contentEl.innerHTML = renderMarkdown(streamingRawText);
        streamingRawText = '';
    }
    isStreaming = false;
    input.disabled = false;
    btn.disabled = false;
    input.focus();
    scrollToBottom();
}

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

function appendMessage(role, text) {
    const container = document.getElementById('chat-messages');
    const wrapper = document.createElement('div');
    wrapper.className = `flex ${role === 'user' ? 'justify-end' : 'justify-start'} fade-in`;

    const bubble = document.createElement('div');
    bubble.className = role === 'user'
        ? 'max-w-[80%] bg-hoop-orange/20 border border-hoop-orange/30 rounded-2xl rounded-tr-sm px-4 py-3 text-sm'
        : 'max-w-[80%] bg-hoop-card border border-hoop-border rounded-2xl rounded-tl-sm px-4 py-3 text-sm';

    const content = document.createElement('div');
    content.className = 'msg-content break-words leading-relaxed prose-chat';
    if (role === 'user') {
        content.textContent = text;
    } else {
        content.innerHTML = text ? renderMarkdown(text) : '';
    }

    bubble.appendChild(content);
    wrapper.appendChild(bubble);
    container.appendChild(wrapper);
    scrollToBottom();
    return wrapper;
}

function scrollToBottom() {
    const container = document.getElementById('chat-container');
    container.scrollTop = container.scrollHeight;
}

// ---------------------------------------------------------------------------
// Source cards
// ---------------------------------------------------------------------------

function renderSourceCards(cards, container) {
    if (!cards || cards.length === 0) {
        container.innerHTML = '<p class="text-xs text-gray-500 italic">No sources found for this response.</p>';
        return;
    }

    container.innerHTML = cards.map(card => `
        <div class="bg-hoop-card rounded-lg overflow-hidden border border-hoop-border hover:border-hoop-orange/40 transition-all fade-in">
            ${card.thumbnail_url ? `
                <a href="${escapeAttr(card.youtube_url)}" target="_blank" rel="noopener">
                    <img src="${escapeAttr(card.thumbnail_url)}" alt="${escapeAttr(card.player1)} vs ${escapeAttr(card.player2)}"
                         class="w-full aspect-video object-cover hover:brightness-110 transition-all" loading="lazy" />
                </a>
            ` : ''}
            <div class="p-3 space-y-1.5">
                <div class="flex items-center justify-between">
                    <span class="text-sm font-bold">
                        ${escapeHtml(card.player1)} <span class="text-gray-500">vs</span> ${escapeHtml(card.player2)}
                    </span>
                    ${card.score ? `
                        <span class="text-xs bg-hoop-orange/20 text-hoop-orange px-1.5 py-0.5 rounded font-mono">
                            ${Math.round(card.score * 100)}%
                        </span>
                    ` : ''}
                </div>
                <div class="flex items-center gap-3 text-xs text-gray-400">
                    ${card.match_date ? `<span>${escapeHtml(card.match_date)}</span>` : ''}
                    ${card.section ? `<span class="capitalize">${escapeHtml(card.section)}</span>` : ''}
                    ${card.views ? `<span>${Number(card.views).toLocaleString()} views</span>` : ''}
                </div>
                ${card.snippet ? `<p class="text-xs text-gray-400 leading-relaxed line-clamp-2">${escapeHtml(card.snippet)}…</p>` : ''}
                <a href="${escapeAttr(card.youtube_url)}" target="_blank" rel="noopener"
                   class="inline-flex items-center gap-1 text-xs text-hoop-orange hover:text-hoop-amber transition-colors mt-1">
                    ▶ Watch on YouTube
                </a>
            </div>
        </div>
    `).join('');
}

// ---------------------------------------------------------------------------
// Mode switching
// ---------------------------------------------------------------------------

function setMode(mode) {
    fetch(`/api/chat/mode/${mode}`, { method: 'POST' });
    // Update button styles
    document.querySelectorAll('.mode-btn').forEach(btn => {
        btn.classList.remove('bg-hoop-orange', 'text-white');
        btn.classList.add('text-gray-400', 'hover:text-white');
    });
    const active = document.getElementById(`mode-${mode}`);
    if (active) {
        active.classList.add('bg-hoop-orange', 'text-white');
        active.classList.remove('text-gray-400', 'hover:text-white');
    }
}

// ---------------------------------------------------------------------------
// Markdown rendering (lightweight — no external library)
// ---------------------------------------------------------------------------

function renderMarkdown(raw) {
    if (!raw) return '';

    // First, escape HTML to prevent XSS
    let text = raw.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

    // Handle route tags (injected by route SSE event, not user text)
    text = text.replace(
        /\{\{route:(.+?)\}\}/g,
        '<span class="system-note">$1</span>'
    );

    // Split into lines for block-level processing
    const lines = text.split('\n');
    const blocks = [];
    let i = 0;

    while (i < lines.length) {
        const line = lines[i];

        // Numbered list item: "1. text" or "1) text"
        if (/^\d+[\.\)]\s+/.test(line)) {
            const items = [];
            while (i < lines.length && /^\d+[\.\)]\s+/.test(lines[i])) {
                items.push('<li>' + inlineFormat(lines[i].replace(/^\d+[\.\)]\s+/, '')) + '</li>');
                i++;
            }
            blocks.push('<ol>' + items.join('') + '</ol>');
            continue;
        }

        // Unordered list item: "- text" or "* text"
        if (/^[\-\*]\s+/.test(line)) {
            const items = [];
            while (i < lines.length && /^[\-\*]\s+/.test(lines[i])) {
                items.push('<li>' + inlineFormat(lines[i].replace(/^[\-\*]\s+/, '')) + '</li>');
                i++;
            }
            blocks.push('<ul>' + items.join('') + '</ul>');
            continue;
        }

        // Indented sub-item: "  - text"
        if (/^\s{2,}[\-\*]\s+/.test(line)) {
            const items = [];
            while (i < lines.length && /^\s{2,}[\-\*]\s+/.test(lines[i])) {
                items.push('<li>' + inlineFormat(lines[i].replace(/^\s+[\-\*]\s+/, '')) + '</li>');
                i++;
            }
            blocks.push('<ul style="margin-left:1em">' + items.join('') + '</ul>');
            continue;
        }

        // Empty line = paragraph break
        if (line.trim() === '') {
            i++;
            continue;
        }

        // Regular paragraph — collect consecutive non-empty, non-list lines
        const paraLines = [];
        while (i < lines.length && lines[i].trim() !== '' && !/^[\-\*]\s+/.test(lines[i]) && !/^\d+[\.\)]\s+/.test(lines[i]) && !/^\s{2,}[\-\*]/.test(lines[i])) {
            paraLines.push(lines[i]);
            i++;
        }
        if (paraLines.length > 0) {
            blocks.push('<p>' + inlineFormat(paraLines.join('<br>')) + '</p>');
        }
    }

    return blocks.join('');
}

function inlineFormat(text) {
    // Bold: **text** or __text__
    text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/__(.+?)__/g, '<strong>$1</strong>');

    // Italic: *text* or _text_ (but not inside URLs)
    text = text.replace(/(?<!\w)\*([^*]+?)\*(?!\w)/g, '<em>$1</em>');

    // Auto-link URLs
    text = text.replace(
        /(https?:\/\/[^\s<]+)/g,
        '<a href="$1" target="_blank" rel="noopener">$1</a>'
    );

    return text;
}

// ---------------------------------------------------------------------------
// Clear chat
// ---------------------------------------------------------------------------

function clearChat() {
    fetch('/api/chat/clear', { method: 'POST' });
    document.getElementById('chat-messages').innerHTML = '';
    document.getElementById('source-cards').innerHTML =
        '<p class="text-xs text-gray-500 italic">Sources will appear here when you ask a question.</p>';
    // Optionally go back to landing
    chatStarted = false;
    document.getElementById('landing').classList.remove('hidden');
    document.getElementById('chat-messages').classList.add('hidden');
    document.getElementById('source-panel').classList.add('hidden');
}

// ---------------------------------------------------------------------------
// Escaping
// ---------------------------------------------------------------------------

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function escapeAttr(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ---------------------------------------------------------------------------
// Data refresh pipeline
// ---------------------------------------------------------------------------

let isRefreshing = false;

const STEP_MAP = {
    'Phase 1: Scrape hooprec.com': 'step-1',
    'Phase 2: YouTube ingest':     'step-2',
    'Phase 3: ChromaDB ingest':    'step-3',
};

function refreshData() {
    if (isRefreshing) return;
    isRefreshing = true;

    // Update button state
    const btn = document.getElementById('refresh-btn');
    const icon = document.getElementById('refresh-icon');
    const label = document.getElementById('refresh-label');
    btn.disabled = true;
    btn.classList.add('opacity-50', 'cursor-not-allowed');
    icon.classList.add('animate-spin');
    label.textContent = 'Refreshing…';

    // Show panel & reset
    const panel = document.getElementById('refresh-panel');
    const logEl = document.getElementById('refresh-log');
    const statusEl = document.getElementById('refresh-status');
    panel.classList.remove('hidden');
    logEl.innerHTML = '';
    statusEl.textContent = 'Starting pipeline…';
    statusEl.className = 'text-hoop-orange font-medium';
    Object.values(STEP_MAP).forEach(id => {
        const el = document.getElementById(id);
        el.className = 'text-gray-500';
        el.textContent = el.textContent.replace(/^[✅❌🔄⏳]\s*/, '⏳ ');
    });

    fetch('/api/ingest/refresh', { method: 'POST' })
        .then(response => {
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            function read() {
                reader.read().then(({ done, value }) => {
                    if (done) { finishRefresh(true); return; }
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop();

                    let eventType = '';
                    for (const line of lines) {
                        if (line.startsWith('event: ')) {
                            eventType = line.slice(7).trim();
                        } else if (line.startsWith('data: ')) {
                            handleRefreshEvent(eventType, line.slice(6), logEl, statusEl);
                        }
                    }
                    read();
                });
            }
            read();
        })
        .catch(err => {
            statusEl.textContent = `Error: ${err.message}`;
            statusEl.className = 'text-red-400 font-medium';
            finishRefresh(false);
        });
}

function handleRefreshEvent(eventType, data, logEl, statusEl) {
    try {
        const payload = JSON.parse(data);
        switch (eventType) {
            case 'progress': {
                const stepId = STEP_MAP[payload.step];
                const el = stepId ? document.getElementById(stepId) : null;
                if (el) {
                    if (payload.status === 'running') {
                        el.className = 'text-hoop-orange';
                        el.textContent = el.textContent.replace(/^[✅❌🔄⏳]\s*/, '🔄 ');
                        statusEl.textContent = payload.step + '…';
                    } else if (payload.status === 'done') {
                        el.className = 'text-green-400';
                        el.textContent = el.textContent.replace(/^[✅❌🔄⏳]\s*/, '✅ ');
                    } else if (payload.status === 'error') {
                        el.className = 'text-red-400';
                        el.textContent = el.textContent.replace(/^[✅❌🔄⏳]\s*/, '❌ ');
                    }
                }
                break;
            }
            case 'log': {
                const line = document.createElement('div');
                line.textContent = payload.line;
                logEl.appendChild(line);
                logEl.scrollTop = logEl.scrollHeight;
                break;
            }
            case 'done':
                statusEl.textContent = 'All steps complete ✓';
                statusEl.className = 'text-green-400 font-medium';
                finishRefresh(true);
                break;
            case 'error':
                statusEl.textContent = `Pipeline error: ${payload.error}`;
                statusEl.className = 'text-red-400 font-medium';
                finishRefresh(false);
                break;
        }
    } catch (e) { /* ignore parse errors */ }
}

function finishRefresh(success) {
    isRefreshing = false;
    const btn = document.getElementById('refresh-btn');
    const icon = document.getElementById('refresh-icon');
    const label = document.getElementById('refresh-label');
    btn.disabled = false;
    btn.classList.remove('opacity-50', 'cursor-not-allowed');
    icon.classList.remove('animate-spin');
    label.textContent = 'Refresh Data';

    // If success, reload landing page game cards after a short delay
    if (success) {
        setTimeout(() => {
            fetch('/api/games/latest?limit=12')
                .then(r => r.text())
                .then(html => {
                    const grid = document.querySelector('#landing .grid');
                    if (grid) grid.innerHTML = html;
                });
        }, 500);
    }
}

function closeRefreshPanel() {
    document.getElementById('refresh-panel').classList.add('hidden');
}
