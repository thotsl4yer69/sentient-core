/**
 * SENTIENT CORE WEB CHAT INTERFACE v2.0
 * Complete rewrite: markdown, toasts, neural dashboard, message polish
 */

// Configure marked for markdown rendering
marked.setOptions({
    highlight: function(code, lang) {
        if (lang && hljs.getLanguage(lang)) {
            return hljs.highlight(code, { language: lang }).value;
        }
        return hljs.highlightAuto(code).value;
    },
    breaks: true,
    gfm: true
});

class ChatInterface {
    constructor() {
        this.ws = null;
        this.reconnectInterval = 3000;
        this.reconnectTimer = null;
        this.isRecording = false;
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.ttsEnabled = false;
        this.messageQueue = [];
        this.dashboardOpen = false;
        this.emotionHistory = [];
        this.activityLog = [];
        this.messageTimestamps = new Map(); // for relative time updates
        this._unreadCount = 0;
        this._userAtBottom = true;

        // Audio context for notification sounds
        this._audioCtx = null;

        // Web Speech API voice recognition
        this.recognition = null;
        this.isListening = false;

        // Browser TTS state
        this.selectedVoice = null;
        this.isSpeaking = false;
        this._piperAudioPlayed = false;

        // DOM elements
        this.messagesContainer = document.getElementById('messages');
        this.messageInput = document.getElementById('message-input');
        this.sendBtn = document.getElementById('send-btn');
        this.voiceBtn = document.getElementById('voice-btn');
        this.ttsBtn = document.getElementById('tts-btn');
        this.connectionStatus = document.getElementById('connection-status');
        this.emotionStatus = document.getElementById('emotion-status');
        this.emotionText = document.getElementById('emotion-text');
        this.thinkingIndicator = document.getElementById('thinking-indicator');
        this.thinkingStage = document.getElementById('thinking-stage');
        this.systemTime = document.getElementById('system-time');
        this.charCount = document.getElementById('char-count');
        this.toastContainer = document.getElementById('toast-container');

        // Command palette
        this.commandPalette = null;
        this._initCommandPalette();

        // Dashboard elements
        this.dashboardEl = document.getElementById('neural-dashboard');
        this.dashboardToggle = document.getElementById('dashboard-toggle');
        this.dashboardClose = document.getElementById('dashboard-close');

        this.init();
    }

    init() {
        this.setupEventListeners();
        this.connectWebSocket();
        this.startSystemClock();
        this.autoResizeTextarea();
        this.startRelativeTimeUpdater();
        this.createScrollButton();
        this.setupScrollDetection();
        this.initVoiceRecognition();
        this.initBrowserTTS();
        this.initInstallPrompt();
    }

    // ========================================================
    // PWA INSTALL PROMPT
    // ========================================================

    initInstallPrompt() {
        // Check if user dismissed the install prompt
        if (localStorage.getItem('cortana-install-dismissed')) {
            return;
        }

        this.deferredPrompt = null;

        // Listen for beforeinstallprompt event
        window.addEventListener('beforeinstallprompt', (e) => {
            // Prevent default browser install prompt
            e.preventDefault();
            this.deferredPrompt = e;

            // Show custom install banner
            this.showInstallBanner();
        });

        // Listen for successful install
        window.addEventListener('appinstalled', () => {
            this.hideInstallBanner();
            this.showNotification('Cortana installed successfully!', 'success');
            this.deferredPrompt = null;
        });
    }

    showInstallBanner() {
        // Check if banner already exists
        if (document.getElementById('install-banner')) {
            return;
        }

        const banner = document.createElement('div');
        banner.id = 'install-banner';
        banner.className = 'install-banner';
        banner.innerHTML = `
            <div class="install-text">Install Cortana on your device</div>
            <button class="btn-install" id="install-btn">INSTALL</button>
            <button class="btn-dismiss" id="install-dismiss">&times;</button>
        `;

        // Insert after header, before chat container
        const chatContainer = document.querySelector('.chat-container');
        chatContainer.parentNode.insertBefore(banner, chatContainer);

        // Setup event listeners
        document.getElementById('install-btn').addEventListener('click', () => {
            this.handleInstallClick();
        });

        document.getElementById('install-dismiss').addEventListener('click', () => {
            this.dismissInstallBanner();
        });
    }

    hideInstallBanner() {
        const banner = document.getElementById('install-banner');
        if (banner) {
            banner.remove();
        }
    }

    async handleInstallClick() {
        if (!this.deferredPrompt) {
            return;
        }

        // Show the install prompt
        this.deferredPrompt.prompt();

        // Wait for user response
        const { outcome } = await this.deferredPrompt.userChoice;

        if (outcome === 'accepted') {
            console.log('User accepted the install prompt');
        } else {
            console.log('User dismissed the install prompt');
        }

        // Clear the deferred prompt
        this.deferredPrompt = null;
        this.hideInstallBanner();
    }

    dismissInstallBanner() {
        localStorage.setItem('cortana-install-dismissed', 'true');
        this.hideInstallBanner();
    }

    // ========================================================
    // NOTIFICATION SOUNDS (Web Audio API - no files needed)
    // ========================================================

    _getAudioCtx() {
        if (!this._audioCtx) {
            this._audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        return this._audioCtx;
    }

    playNotificationSound(type = 'message') {
        try {
            const ctx = this._getAudioCtx();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.connect(gain);
            gain.connect(ctx.destination);

            if (type === 'message') {
                // Soft two-tone chime
                osc.frequency.setValueAtTime(587, ctx.currentTime); // D5
                osc.frequency.setValueAtTime(880, ctx.currentTime + 0.08); // A5
                gain.gain.setValueAtTime(0.06, ctx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
                osc.type = 'sine';
                osc.start(ctx.currentTime);
                osc.stop(ctx.currentTime + 0.25);
            } else if (type === 'send') {
                // Quick blip up
                osc.frequency.setValueAtTime(440, ctx.currentTime);
                osc.frequency.exponentialRampToValueAtTime(660, ctx.currentTime + 0.06);
                gain.gain.setValueAtTime(0.04, ctx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.1);
                osc.type = 'sine';
                osc.start(ctx.currentTime);
                osc.stop(ctx.currentTime + 0.1);
            } else if (type === 'proactive') {
                // Gentle three-note descending chime (distinct from regular message)
                osc.frequency.setValueAtTime(784, ctx.currentTime);     // G5
                osc.frequency.setValueAtTime(659, ctx.currentTime + 0.12); // E5
                osc.frequency.setValueAtTime(523, ctx.currentTime + 0.24); // C5
                gain.gain.setValueAtTime(0.05, ctx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
                osc.type = 'sine';
                osc.start(ctx.currentTime);
                osc.stop(ctx.currentTime + 0.4);
            } else if (type === 'connect') {
                // Rising triad
                osc.frequency.setValueAtTime(523, ctx.currentTime); // C5
                osc.frequency.setValueAtTime(659, ctx.currentTime + 0.1); // E5
                osc.frequency.setValueAtTime(784, ctx.currentTime + 0.2); // G5
                gain.gain.setValueAtTime(0.05, ctx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
                osc.type = 'sine';
                osc.start(ctx.currentTime);
                osc.stop(ctx.currentTime + 0.4);
            }
        } catch (e) {
            // Audio not available - silently fail
        }
    }

    // ========================================================
    // SCROLL TO BOTTOM BUTTON
    // ========================================================

    createScrollButton() {
        const btn = document.createElement('button');
        btn.className = 'scroll-to-bottom';
        btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg><span class="unread-badge" style="display:none">0</span>';
        btn.addEventListener('click', () => {
            this.scrollToBottom();
            this._unreadCount = 0;
            btn.querySelector('.unread-badge').style.display = 'none';
            btn.classList.remove('visible');
        });
        document.querySelector('.container').appendChild(btn);
        this._scrollBtn = btn;
    }

    setupScrollDetection() {
        this.messagesContainer.addEventListener('scroll', () => {
            const { scrollTop, scrollHeight, clientHeight } = this.messagesContainer;
            this._userAtBottom = (scrollHeight - scrollTop - clientHeight) < 60;

            if (this._userAtBottom) {
                this._unreadCount = 0;
                if (this._scrollBtn) {
                    this._scrollBtn.classList.remove('visible');
                    this._scrollBtn.querySelector('.unread-badge').style.display = 'none';
                }
            }
        });
    }

    _showScrollButton() {
        if (!this._userAtBottom && this._scrollBtn) {
            this._scrollBtn.classList.add('visible');
            if (this._unreadCount > 0) {
                const badge = this._scrollBtn.querySelector('.unread-badge');
                badge.textContent = this._unreadCount;
                badge.style.display = 'flex';
            }
        }
    }

    // ========================================================
    // MARKDOWN RENDERING
    // ========================================================

    renderMarkdown(text) {
        if (!text) return '';
        try {
            const rawHtml = marked.parse(text);
            return DOMPurify.sanitize(rawHtml, {
                ADD_TAGS: ['code', 'pre', 'span'],
                ADD_ATTR: ['class']
            });
        } catch (e) {
            console.error('Markdown render error:', e);
            return this.escapeHtml(text);
        }
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // ========================================================
    // COMMAND PALETTE
    // ========================================================

    _initCommandPalette() {
        // Create palette element
        const palette = document.createElement('div');
        palette.className = 'command-palette';
        palette.id = 'command-palette';
        palette.style.display = 'none';
        palette.innerHTML = `
            <div class="palette-header">QUICK COMMANDS</div>
            <div class="palette-grid">
                <button class="palette-cmd" data-cmd="run a full diagnostic">
                    <span class="palette-icon">&#9881;</span>
                    <span class="palette-label">Diagnostic</span>
                </button>
                <button class="palette-cmd" data-cmd="show network status">
                    <span class="palette-icon">&#9732;</span>
                    <span class="palette-label">Network</span>
                </button>
                <button class="palette-cmd" data-cmd="what's your current status?">
                    <span class="palette-icon">&#9889;</span>
                    <span class="palette-label">Status</span>
                </button>
                <button class="palette-cmd" data-cmd="how are you feeling right now?">
                    <span class="palette-icon">&#9829;</span>
                    <span class="palette-label">Mood</span>
                </button>
                <button class="palette-cmd" data-cmd="show me the service status">
                    <span class="palette-icon">&#9635;</span>
                    <span class="palette-label">Services</span>
                </button>
                <button class="palette-cmd" data-cmd="what do you remember about me?">
                    <span class="palette-icon">&#9733;</span>
                    <span class="palette-label">Memory</span>
                </button>
                <button class="palette-cmd" data-cmd="run a network scan">
                    <span class="palette-icon">&#8982;</span>
                    <span class="palette-label">Scan</span>
                </button>
                <button class="palette-cmd" data-cmd="check the system logs">
                    <span class="palette-icon">&#9776;</span>
                    <span class="palette-label">Logs</span>
                </button>
            </div>
        `;

        // Insert before input area
        const inputArea = document.querySelector('.input-area');
        if (inputArea) inputArea.parentNode.insertBefore(palette, inputArea);
        this.commandPalette = palette;

        // Handle clicks
        palette.querySelectorAll('.palette-cmd').forEach(btn => {
            btn.addEventListener('click', () => {
                const cmd = btn.dataset.cmd;
                this.messageInput.value = cmd;
                this._hideCommandPalette();
                this.sendMessage();
            });
        });
    }

    _showCommandPalette() {
        if (this.commandPalette) {
            this.commandPalette.style.display = 'block';
            this.commandPalette.classList.add('palette-visible');
        }
    }

    _hideCommandPalette() {
        if (this.commandPalette) {
            this.commandPalette.classList.remove('palette-visible');
            setTimeout(() => { this.commandPalette.style.display = 'none'; }, 200);
        }
    }

    // ========================================================
    // TOAST NOTIFICATION SYSTEM
    // ========================================================

    showNotification(message, type = 'info', duration = 4000) {
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;

        const icons = {
            info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
            success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
            warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
            error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>'
        };

        toast.innerHTML = `
            <div class="toast-icon">${icons[type] || icons.info}</div>
            <div class="toast-message">${this.escapeHtml(message)}</div>
            <button class="toast-dismiss">&times;</button>
        `;

        toast.querySelector('.toast-dismiss').addEventListener('click', () => {
            toast.classList.add('toast-exit');
            setTimeout(() => toast.remove(), 300);
        });

        this.toastContainer.appendChild(toast);

        // Auto-remove
        setTimeout(() => {
            if (toast.parentNode) {
                toast.classList.add('toast-exit');
                setTimeout(() => toast.remove(), 300);
            }
        }, duration);

        // Log to activity
        this.addActivityLogEntry(type, message);
    }

    // ========================================================
    // EVENT LISTENERS
    // ========================================================

    setupEventListeners() {
        // Send message
        this.sendBtn.addEventListener('click', () => this.sendMessage());

        this.messageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Character counter
        this.messageInput.addEventListener('input', () => {
            this.charCount.textContent = this.messageInput.value.length;
            this.autoResizeTextarea();
        });

        // Command palette trigger
        this.messageInput.addEventListener('input', () => {
            if (this.messageInput.value === '/') {
                this._showCommandPalette();
            } else {
                this._hideCommandPalette();
            }
        });

        // Voice input
        this.voiceBtn.addEventListener('click', () => this.toggleVoiceInput());

        // TTS toggle
        this.ttsBtn.addEventListener('click', () => this.toggleTTS());

        // Dashboard toggle
        if (this.dashboardToggle) {
            this.dashboardToggle.addEventListener('click', () => this.toggleDashboard());
        }
        if (this.dashboardClose) {
            this.dashboardClose.addEventListener('click', () => this.toggleDashboard(false));
        }

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            // Escape to close dashboard or clear input
            if (e.key === 'Escape') {
                if (this.dashboardOpen) {
                    this.toggleDashboard(false);
                } else if (this.messageInput.value) {
                    this.messageInput.value = '';
                    this.charCount.textContent = '0';
                    this.autoResizeTextarea();
                }
            }
            // Ctrl+D to toggle dashboard
            if (e.ctrlKey && e.key === 'd') {
                e.preventDefault();
                this.toggleDashboard();
            }
            // Ctrl+E to export conversation
            if (e.ctrlKey && e.key === 'e') {
                e.preventDefault();
                this.exportConversation();
            }
            // Ctrl+F: Search messages
            if (e.ctrlKey && e.key === 'f') {
                e.preventDefault();
                this.toggleMessageSearch();
                return;
            }
            // ? to show shortcuts (only when not typing)
            if (e.key === '?' && document.activeElement !== this.messageInput) {
                e.preventDefault();
                this.showShortcutsOverlay();
            }
        });

        // Prevent accidental page unload during typing
        window.addEventListener('beforeunload', (e) => {
            if (this.messageInput.value.trim().length > 0) {
                e.preventDefault();
                e.returnValue = '';
            }
        });
    }

    autoResizeTextarea() {
        const textarea = this.messageInput;
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
    }

    // ========================================================
    // WEBSOCKET
    // ========================================================

    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        this.updateConnectionStatus('connecting');

        try {
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                this.updateConnectionStatus('connected');
                this.clearReconnectTimer();
                this.playNotificationSound('connect');
                this.processMessageQueue();
                this.showNotification('Neural link established', 'success', 2000);
            };

            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage(data);
                } catch (error) {
                    console.error('Error parsing WebSocket message:', error);
                }
            };

            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                this.updateConnectionStatus('error');
            };

            this.ws.onclose = () => {
                this.updateConnectionStatus('disconnected');
                this.scheduleReconnect();
            };

        } catch (error) {
            console.error('Failed to create WebSocket:', error);
            this.updateConnectionStatus('error');
            this.scheduleReconnect();
        }
    }

    handleWebSocketMessage(data) {
        const { type } = data;

        try {
            switch (type) {
                case 'history':
                    this.loadMessageHistory(data.messages);
                    break;

                case 'message':
                    this.addMessage(data.message);
                    break;

                case 'emotion':
                    this.updateEmotion(data.emotion, data.intensity);
                    break;

                case 'stream':
                    this.handleStreamToken(data);
                    break;

                case 'thinking':
                    this.updateThinking(data.active, data.stage);
                    break;

                case 'tts_status':
                    this.updateTTSStatus(data.status, data.progress);
                    break;

                case 'tts_audio':
                    this.playTTSAudio(data.audio, data.format, data.phonemes, data.duration);
                    break;

                case 'tts_phonemes':
                    if (window.avatarRenderer && typeof window.avatarRenderer.processPhonemes === 'function') {
                        window.avatarRenderer.processPhonemes({
                            phonemes: data.phonemes,
                            duration: data.duration
                        });
                    }
                    break;

                case 'speaking':
                    if (window.avatarRenderer && typeof window.avatarRenderer.setSpeaking === 'function') {
                        window.avatarRenderer.setSpeaking(data.active, data.text || '');
                    }
                    break;

                case 'welcome':
                    this.showWelcomeMessage(data);
                    break;

                case 'system_status':
                    this.updateDashboard(data);
                    break;

                case 'mqtt_status':
                    if (data.status === 'disconnected') {
                        this.showNotification('MQTT link lost', 'warning');
                    } else if (data.status === 'connected') {
                        this.addActivityLogEntry('info', 'MQTT bridge connected');
                    }
                    break;

                case 'error':
                    this.showNotification(data.message, 'error');
                    break;

                case 'diagnostic_request':
                    if (window.avatarRenderer && window.avatarRenderer.getDiagnostics) {
                        const diagData = window.avatarRenderer.getDiagnostics();
                        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                            this.ws.send(JSON.stringify({
                                type: 'diagnostic_response',
                                data: diagData
                            }));
                        }
                    }
                    break;

                case 'pong':
                    break;

                default:
                    console.log('Unknown message type:', type, data);
            }
        } catch (error) {
            console.error(`Error handling WebSocket message type "${type}":`, error);
        }
    }

    // ========================================================
    // MESSAGES
    // ========================================================

    sendMessage() {
        const text = this.messageInput.value.trim();

        if (!text) return;

        if (text.length > 2000) {
            this.showNotification('Message too long (max 2000 characters)', 'error');
            return;
        }

        const message = { type: 'message', text: text };

        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
            this.playNotificationSound('send');
            this.messageInput.value = '';
            this.charCount.textContent = '0';
            this.autoResizeTextarea();
            this.messageInput.focus();

            if (window.avatarRenderer && typeof window.avatarRenderer.updateFromChatMessage === 'function') {
                window.avatarRenderer.updateFromChatMessage('user', text);
            }
        } else {
            this.messageQueue.push(message);
            this.showNotification('Message queued - reconnecting...', 'warning');
        }
    }

    processMessageQueue() {
        while (this.messageQueue.length > 0) {
            const message = this.messageQueue.shift();
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify(message));
            }
        }
    }

    createMessageElement(role, content, timestamp, emotion, isStreaming = false) {
        const messageEl = document.createElement('div');
        messageEl.className = `message ${role}`;
        if (isStreaming) messageEl.id = 'streaming-message';

        // SVG Avatar icons
        const userAvatarSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="20" height="20"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
        const cortanaAvatarSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="20" height="20"><polygon points="12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5"/><circle cx="12" cy="12" r="3"/></svg>';

        // Avatar
        const avatarEl = document.createElement('div');
        avatarEl.className = 'message-avatar';
        avatarEl.innerHTML = role === 'user' ? userAvatarSvg : cortanaAvatarSvg;

        // Content wrapper
        const contentEl = document.createElement('div');
        contentEl.className = 'message-content';

        // Bubble
        const bubbleEl = document.createElement('div');
        bubbleEl.className = 'message-bubble';

        if (role === 'assistant' && content && !isStreaming) {
            bubbleEl.innerHTML = this.renderMarkdown(content);
            // Syntax highlight any code blocks
            bubbleEl.querySelectorAll('pre code').forEach(block => {
                hljs.highlightElement(block);
            });
        } else {
            bubbleEl.textContent = content || '';
        }

        // Meta row (timestamp + actions)
        const metaEl = document.createElement('div');
        metaEl.className = 'message-meta';

        // Relative timestamp
        const timeEl = document.createElement('span');
        timeEl.className = 'message-timestamp';
        const ts = timestamp ? new Date(timestamp) : new Date();
        timeEl.textContent = this.getRelativeTime(ts);
        timeEl.dataset.timestamp = ts.getTime();
        this.messageTimestamps.set(timeEl, ts);

        metaEl.appendChild(timeEl);

        // Copy button (for assistant messages)
        if (role === 'assistant' && content && !isStreaming) {
            const copyBtn = document.createElement('button');
            copyBtn.className = 'btn-copy';
            copyBtn.title = 'Copy message';
            copyBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
            copyBtn.addEventListener('click', () => {
                navigator.clipboard.writeText(content).then(() => {
                    copyBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><polyline points="20 6 9 17 4 12"/></svg>';
                    setTimeout(() => {
                        copyBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
                    }, 1500);
                });
            });
            metaEl.appendChild(copyBtn);
        }

        contentEl.appendChild(bubbleEl);
        contentEl.appendChild(metaEl);

        // Add feedback buttons to assistant messages (below meta row)
        if (role === 'assistant' && content && !isStreaming) {
            const feedbackDiv = document.createElement('div');
            feedbackDiv.className = 'message-feedback';
            feedbackDiv.innerHTML = `
                <button class="feedback-btn feedback-up" title="Good response" data-feedback="up">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                        <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/>
                    </svg>
                </button>
                <button class="feedback-btn feedback-down" title="Could be better" data-feedback="down">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                        <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/>
                    </svg>
                </button>
            `;
            feedbackDiv.querySelectorAll('.feedback-btn').forEach(btn => {
                btn.addEventListener('click', (event) => this.sendFeedback(event, btn.dataset.feedback, content.substring(0, 100)));
            });
            contentEl.appendChild(feedbackDiv);
        }
        messageEl.appendChild(avatarEl);
        messageEl.appendChild(contentEl);

        // Add copy buttons to code blocks after element is created
        if (role === 'assistant' && content && !isStreaming) {
            requestAnimationFrame(() => {
                messageEl.querySelectorAll('pre code').forEach(block => {
                    const btn = document.createElement('button');
                    btn.className = 'code-copy-btn';
                    btn.textContent = 'Copy';
                    btn.addEventListener('click', () => {
                        navigator.clipboard.writeText(block.textContent).then(() => {
                            btn.textContent = 'Copied!';
                            btn.classList.add('copied');
                            setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 2000);
                        });
                    });
                    block.parentElement.style.position = 'relative';
                    block.parentElement.appendChild(btn);
                });
            });
        }

        return { messageEl, bubbleEl };
    }

    addMessage(messageData) {
        const { role, content, timestamp, emotion } = messageData;
        const isProactive = messageData.proactive === true;

        // Remove streaming element if final assistant message arrives
        if (role === 'assistant') {
            const streamEl = document.getElementById('streaming-message');
            if (streamEl) streamEl.remove();
            this._streamingEl = null;
            this._streamingText = '';
        }

        const { messageEl } = this.createMessageElement(role, content, timestamp, emotion);

        // Add proactive visual indicator
        if (isProactive) {
            messageEl.classList.add('proactive');
            const triggerType = messageData.trigger_type || 'observation';
            const labelMap = {
                'boot': 'SYSTEM ONLINE',
                'boredom': 'INITIATED CONTACT',
                'concern': 'SECURITY ALERT',
                'curiosity': 'OBSERVATION',
                'care': 'CHECK-IN',
                'excitement': 'SYSTEM EVENT',
                'system_observation': 'SYSTEM MONITOR',
                'idle_thought': 'IDLE THOUGHT',
                'reminder': 'REMINDER',
                'daily_briefing': 'DAILY BRIEFING',
                'network_event': 'NETWORK ALERT',
                'memory_followup': 'MEMORY RECALL',
                'night_owl': 'NIGHT OWL',
                'streak_tracker': 'STREAK',
                'conversation_recap': 'RECAP',
                'learning_moment': 'CURIOUS',
                'weather_alert': 'WEATHER',
                'first_morning_greeting': 'GOOD MORNING'
            };
            const labelText = labelMap[triggerType] || 'AUTONOMOUS';
            const label = document.createElement('div');
            label.className = 'proactive-label';
            label.textContent = labelText;
            const bubble = messageEl.querySelector('.message-bubble');
            if (bubble) bubble.prepend(label);
        }

        this.messagesContainer.appendChild(messageEl);

        // Sound + scroll handling
        if (role === 'assistant') {
            this.playNotificationSound(isProactive ? 'proactive' : 'message');
            if (!this._userAtBottom) {
                this._unreadCount++;
                this._showScrollButton();
            }
        }
        if (this._userAtBottom) this.scrollToBottom();

        // Hide thinking indicator when assistant responds
        if (role === 'assistant') {
            this.updateThinking(false);

            if (window.avatarRenderer) {
                if (typeof window.avatarRenderer.updateFromChatMessage === 'function') {
                    window.avatarRenderer.updateFromChatMessage(role, content);
                }
                if (emotion && typeof window.avatarRenderer.setEmotion === 'function') {
                    window.avatarRenderer.setEmotion(emotion, 0.8);
                } else if (typeof window.avatarRenderer.setEmotion === 'function') {
                    window.avatarRenderer.setEmotion('neutral', 1.0);
                }
            }

            // Browser TTS fallback: speak if TTS enabled and no Piper audio played
            if (this.ttsEnabled && !this._piperAudioPlayed && content) {
                this.speakText(content);
            }
            // Reset Piper audio flag
            this._piperAudioPlayed = false;
        }

        // Log proactive messages to activity feed
        if (isProactive) {
            this.addActivityLogEntry('proactive', content.substring(0, 60));
        }
    }

    handleStreamToken(data) {
        const { token, done } = data;

        if (done) {
            // Stream complete - finalize with markdown rendering
            if (this._streamingEl && this._streamingText) {
                this._streamingEl.innerHTML = this.renderMarkdown(this._streamingText);
                this._streamingEl.querySelectorAll('pre code').forEach(block => {
                    hljs.highlightElement(block);
                });

                // Add copy button to the streaming message's meta
                const streamMsg = document.getElementById('streaming-message');
                if (streamMsg) {
                    const metaEl = streamMsg.querySelector('.message-meta');
                    if (metaEl && !metaEl.querySelector('.btn-copy')) {
                        const text = this._streamingText;
                        const copyBtn = document.createElement('button');
                        copyBtn.className = 'btn-copy';
                        copyBtn.title = 'Copy message';
                        copyBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
                        copyBtn.addEventListener('click', () => {
                            navigator.clipboard.writeText(text).then(() => {
                                copyBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><polyline points="20 6 9 17 4 12"/></svg>';
                                setTimeout(() => {
                                    copyBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
                                }, 1500);
                            });
                        });
                        metaEl.appendChild(copyBtn);
                    }
                }

                // Browser TTS fallback: speak if no Piper audio was played
                if (this.ttsEnabled && !this._piperAudioPlayed) {
                    this.speakText(this._streamingText);
                }
            }

            if (window.avatarRenderer?.setState) window.avatarRenderer.setState('idle');
            this._streamingEl = null;
            // Reset Piper audio flag for next message
            this._piperAudioPlayed = false;

            // Pulse on completion
            if (window.neuralPulse) window.neuralPulse();
            return;
        }

        if (!token) return;

        // Create or update streaming message element
        if (!this._streamingEl) {
            this.updateThinking(false);

            const { messageEl, bubbleEl } = this.createMessageElement('assistant', '', null, null, true);
            this.messagesContainer.appendChild(messageEl);
            this._streamingEl = bubbleEl;
            this._streamingText = '';
            if (window.avatarRenderer?.setState) window.avatarRenderer.setState('processing');
        }

        if (window.avatarRenderer?.onStreamToken) window.avatarRenderer.onStreamToken();
        this._streamingText += token;
        // Show plain text during streaming with blinking cursor
        this._streamingEl.textContent = this._streamingText;
        // Add/reattach blinking cursor
        let cursor = this._streamingEl.querySelector('.streaming-cursor');
        if (!cursor) {
            cursor = document.createElement('span');
            cursor.className = 'streaming-cursor';
        }
        this._streamingEl.appendChild(cursor);
        if (this._userAtBottom) this.scrollToBottom();
    }

    sendFeedback(event, type, snippet) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'feedback',
                feedback: type,
                snippet: snippet,
                timestamp: Date.now() / 1000
            }));
        }
        const btn = event.target.closest('.feedback-btn');
        if (btn) btn.classList.add('feedback-sent');
        this.showNotification(type === 'up' ? 'Thanks! Noted.' : "Got it, I'll adjust.", 'info');
    }

    loadMessageHistory(messages) {
        this.messagesContainer.innerHTML = '';
        messages.forEach(msg => this.addMessage(msg));
    }

    // ========================================================
    // WELCOME MESSAGE
    // ========================================================

    showWelcomeMessage(data) {
        const text = data.text || 'Neural link established. How can I help?';
        const services = data.services || {};
        const stats = data.stats || {};

        // Build a system-aware welcome
        const messageEl = document.createElement('div');
        messageEl.className = 'message assistant welcome-message';

        const cortanaAvatarSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="20" height="20"><polygon points="12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5"/><circle cx="12" cy="12" r="3"/></svg>';

        const avatarEl = document.createElement('div');
        avatarEl.className = 'message-avatar';
        avatarEl.innerHTML = cortanaAvatarSvg;

        const contentEl = document.createElement('div');
        contentEl.className = 'message-content';

        const bubbleEl = document.createElement('div');
        bubbleEl.className = 'message-bubble';
        bubbleEl.innerHTML = this.renderMarkdown(text);

        // System status bar under welcome
        if (Object.keys(services).length > 0) {
            const statusBar = document.createElement('div');
            statusBar.className = 'welcome-status-bar';
            const activeCount = Object.values(services).filter(s => s === 'active').length;
            const total = Object.keys(services).length;
            statusBar.innerHTML = `<span class="welcome-stat"><span class="stat-dot stat-dot-ok"></span>${activeCount}/${total} systems online</span>`;
            if (stats.gpu) {
                statusBar.innerHTML += `<span class="welcome-stat"><span class="stat-dot stat-dot-ok"></span>GPU ${stats.gpu}</span>`;
            }
            bubbleEl.appendChild(statusBar);
        }

        const metaEl = document.createElement('div');
        metaEl.className = 'message-meta';
        const timeEl = document.createElement('span');
        timeEl.className = 'message-timestamp';
        timeEl.textContent = 'just now';
        metaEl.appendChild(timeEl);

        contentEl.appendChild(bubbleEl);
        contentEl.appendChild(metaEl);
        messageEl.appendChild(avatarEl);
        messageEl.appendChild(contentEl);

        this.messagesContainer.appendChild(messageEl);
        this.scrollToBottom();

        // Set mood from server data
        if (data.mood && data.mood.emotion) {
            this.updateEmotion(data.mood.emotion, data.mood.intensity || 0.5);
        }
    }

    // ========================================================
    // EMOTION SYSTEM
    // ========================================================

    updateEmotion(emotion, intensity = 0.5) {
        this.emotionText.textContent = emotion.toUpperCase();

        const indicator = this.emotionStatus.querySelector('.emotion-indicator');

        const emotionColors = {
            neutral: '#00ffff', calm: '#00ffff',
            happy: '#00ff00', pleased: '#00ff00', joyful: '#00ff00',
            excited: '#ffaa00', curious: '#ff00ff',
            concerned: '#ff6600', worried: '#ff6600',
            empathetic: '#ff9944', sympathetic: '#ff9944',
            sad: '#0066ff', angry: '#ff0066',
            thinking: '#00cccc', focused: '#00cccc', analyzing: '#00cccc',
            surprised: '#ffff00', alert: '#ffff00',
            explaining: '#88ccff', affectionate: '#ff66cc'
        };

        const color = emotionColors[emotion] || '#00ffff';
        indicator.style.background = color;
        const glowIntensity = 15 + (intensity * 20);
        indicator.style.boxShadow = `0 0 ${glowIntensity}px ${color}`;

        // Track emotion history for dashboard
        this.emotionHistory.push({
            emotion, color, intensity,
            time: Date.now()
        });
        if (this.emotionHistory.length > 50) this.emotionHistory.shift();
        this.drawEmotionTrace();

        // Activity log
        this.addActivityLogEntry('emotion', `${emotion} (${Math.round(intensity * 100)}%)`);

        // Update avatar emotion
        if (window.avatarRenderer && typeof window.avatarRenderer.setEmotion === 'function') {
            window.avatarRenderer.setEmotion(emotion, intensity);
        }

        // Update neural background mood
        if (window.setNeuralMood) {
            window.setNeuralMood(emotion);
        }
    }

    // ========================================================
    // THINKING INDICATOR
    // ========================================================

    updateThinking(active, stage = '') {
        if (active) {
            this.thinkingIndicator.style.display = 'block';
            this.thinkingStage.textContent = stage ? `// ${stage}` : '';
            this.addActivityLogEntry('thinking', stage || 'processing');
        } else {
            this.thinkingIndicator.style.display = 'none';
        }

        if (window.avatarRenderer) {
            if (active) {
                if (typeof window.avatarRenderer.setEmotion === 'function') {
                    window.avatarRenderer.setEmotion('thinking', 0.6);
                }
                if (typeof window.avatarRenderer.setAttentionState === 'function') {
                    window.avatarRenderer.setAttentionState('focused');
                }
            }
        }

        this.scrollToBottom();
    }

    // ========================================================
    // VOICE / TTS
    // ========================================================

    initVoiceRecognition() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            console.warn('[Voice] Speech recognition not supported');
            if (this.voiceBtn) this.voiceBtn.style.display = 'none';
            return;
        }

        this.recognition = new SpeechRecognition();
        this.recognition.continuous = false;
        this.recognition.interimResults = true;
        this.recognition.lang = 'en-AU';  // Jack is in Melbourne
        this.recognition.maxAlternatives = 1;

        this.recognition.onstart = () => {
            this.isListening = true;
            if (this.voiceBtn) this.voiceBtn.classList.add('listening');
            // Show listening indicator
            if (this.messageInput) this.messageInput.placeholder = 'LISTENING...';
        };

        this.recognition.onresult = (event) => {
            let finalTranscript = '';
            let interimTranscript = '';

            for (let i = event.resultIndex; i < event.results.length; i++) {
                if (event.results[i].isFinal) {
                    finalTranscript += event.results[i][0].transcript;
                } else {
                    interimTranscript += event.results[i][0].transcript;
                }
            }

            if (this.messageInput) {
                if (finalTranscript) {
                    this.messageInput.value = finalTranscript;
                    // Auto-send after final result
                    setTimeout(() => {
                        if (this.sendBtn) this.sendBtn.click();
                    }, 300);
                } else {
                    this.messageInput.value = interimTranscript;
                }
                // Trigger input event for char counter
                this.messageInput.dispatchEvent(new Event('input'));
            }
        };

        this.recognition.onerror = (event) => {
            console.warn('[Voice] Error:', event.error);
            this.stopListening();
            if (event.error === 'not-allowed') {
                this.showNotification('Microphone access denied. Check browser permissions.', 'error');
            } else if (event.error === 'no-speech') {
                this.showNotification('No speech detected. Try again.', 'warning');
            }
        };

        this.recognition.onend = () => {
            this.stopListening();
        };
    }

    startListening() {
        if (!this.recognition) return;
        try {
            this.recognition.start();
        } catch (e) {
            console.warn('[Voice] Start error:', e);
        }
    }

    stopListening() {
        this.isListening = false;
        if (this.voiceBtn) this.voiceBtn.classList.remove('listening');
        if (this.messageInput && this.messageInput.placeholder === 'LISTENING...') {
            this.messageInput.placeholder = 'TRANSMIT MESSAGE TO CORTANA...';
        }
        try {
            if (this.recognition) this.recognition.stop();
        } catch (e) {}
    }

    async toggleVoiceInput() {
        if (this.isListening) {
            this.stopListening();
        } else {
            this.startListening();
        }
    }

    toggleTTS() {
        this.ttsEnabled = !this.ttsEnabled;

        if (this.ttsEnabled) {
            this.ttsBtn.classList.add('active');
            this.showNotification('Voice output enabled', 'success', 2000);
        } else {
            this.ttsBtn.classList.remove('active');
            this.showNotification('Voice output disabled', 'info', 2000);
            // Cancel any ongoing browser speech
            if ('speechSynthesis' in window) {
                speechSynthesis.cancel();
            }
        }

        const message = { type: 'tts_toggle', enabled: this.ttsEnabled };
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
        }
    }

    playTTSAudio(audioBase64, format, phonemes, duration) {
        if (!this.ttsEnabled || !audioBase64) return;

        // Mark that Piper audio was played for this message
        this._piperAudioPlayed = true;

        try {
            const byteCharacters = atob(audioBase64);
            const byteNumbers = new Array(byteCharacters.length);
            for (let i = 0; i < byteCharacters.length; i++) {
                byteNumbers[i] = byteCharacters.charCodeAt(i);
            }
            const byteArray = new Uint8Array(byteNumbers);
            const blob = new Blob([byteArray], { type: `audio/${format || 'wav'}` });
            const audioUrl = URL.createObjectURL(blob);

            const audioPlayer = document.getElementById('audio-player');
            audioPlayer.src = audioUrl;

            this.ttsBtn.classList.add('playing');

            audioPlayer.onended = () => {
                URL.revokeObjectURL(audioUrl);
                this.ttsBtn.classList.remove('playing');
                if (window.avatarRenderer) window.avatarRenderer.setSpeaking(false);
            };

            audioPlayer.onerror = (e) => {
                console.error('Audio playback error:', e);
                URL.revokeObjectURL(audioUrl);
                this.ttsBtn.classList.remove('playing');
                if (window.avatarRenderer) window.avatarRenderer.setSpeaking(false);
            };

            if (phonemes && phonemes.length > 0 && window.avatarRenderer &&
                typeof window.avatarRenderer.processPhonemes === 'function') {
                window.avatarRenderer.processPhonemes({
                    phonemes: phonemes,
                    duration: duration
                });
            }

            audioPlayer.play().catch(e => {
                console.error('Audio play failed:', e);
                this.ttsBtn.classList.remove('playing');
                if (window.avatarRenderer) window.avatarRenderer.setSpeaking(false);
            });
        } catch (error) {
            console.error('TTS audio decode error:', error);
        }
    }

    updateTTSStatus(status, progress) {
        console.log('TTS status:', status, progress);
    }

    // ========================================================
    // BROWSER TEXT-TO-SPEECH (Web Speech API)
    // ========================================================

    initBrowserTTS() {
        if (!('speechSynthesis' in window)) {
            console.warn('[TTS] Browser speech synthesis not supported');
            return;
        }

        // Load voices (may be async)
        const loadVoices = () => {
            const voices = speechSynthesis.getVoices();
            if (voices.length === 0) return;

            // Prefer: 1) Australian English female, 2) Any English female, 3) Any English voice
            this.selectedVoice = voices.find(v => v.lang === 'en-AU' && v.name.toLowerCase().includes('female')) ||
                            voices.find(v => v.lang.startsWith('en') && v.name.toLowerCase().includes('female')) ||
                            voices.find(v => v.lang.startsWith('en') && (v.name.includes('Samantha') || v.name.includes('Karen') || v.name.includes('Moira'))) ||
                            voices.find(v => v.lang.startsWith('en'));

            if (this.selectedVoice) {
                console.log('[TTS] Selected voice:', this.selectedVoice.name, this.selectedVoice.lang);
            }
        };

        loadVoices();
        speechSynthesis.onvoiceschanged = loadVoices;
    }

    speakText(text) {
        if (!('speechSynthesis' in window) || !this.ttsEnabled) return;

        // Cancel any current speech
        speechSynthesis.cancel();

        // Strip markdown formatting for cleaner speech
        const cleanText = text
            .replace(/```[\s\S]*?```/g, 'code block')  // code blocks
            .replace(/`([^`]+)`/g, '$1')  // inline code
            .replace(/\*\*([^*]+)\*\*/g, '$1')  // bold
            .replace(/\*([^*]+)\*/g, '$1')  // italic
            .replace(/#{1,6}\s/g, '')  // headers
            .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')  // links
            .replace(/[*_~`#>-]/g, '')  // remaining markdown
            .trim();

        if (!cleanText) return;

        const utterance = new SpeechSynthesisUtterance(cleanText);
        if (this.selectedVoice) utterance.voice = this.selectedVoice;
        utterance.rate = 1.0;
        utterance.pitch = 1.05;  // Slightly higher for feminine tone
        utterance.volume = 0.9;

        utterance.onstart = () => {
            this.isSpeaking = true;
            if (this.ttsBtn) this.ttsBtn.classList.add('playing');
            // Notify avatar
            if (window.avatarRenderer) window.avatarRenderer.setSpeaking(true);
        };

        utterance.onend = () => {
            this.isSpeaking = false;
            if (this.ttsBtn) this.ttsBtn.classList.remove('playing');
            if (window.avatarRenderer) window.avatarRenderer.setSpeaking(false);
        };

        utterance.onerror = (event) => {
            console.warn('[TTS] Speech error:', event.error);
            this.isSpeaking = false;
            if (this.ttsBtn) this.ttsBtn.classList.remove('playing');
            if (window.avatarRenderer) window.avatarRenderer.setSpeaking(false);
        };

        speechSynthesis.speak(utterance);
    }

    // ========================================================
    // NEURAL DASHBOARD
    // ========================================================

    toggleDashboard(forceState) {
        this.dashboardOpen = forceState !== undefined ? forceState : !this.dashboardOpen;
        this.dashboardEl.classList.toggle('open', this.dashboardOpen);
        this.dashboardToggle.classList.toggle('active', this.dashboardOpen);

        if (this.dashboardOpen) {
            this.fetchSystemStatus();
            // Auto-refresh every 10s while open
            this._dashboardInterval = setInterval(() => {
                if (this.dashboardOpen) this.fetchSystemStatus();
                else clearInterval(this._dashboardInterval);
            }, 10000);
        } else {
            if (this._dashboardInterval) clearInterval(this._dashboardInterval);
        }
    }

    async fetchSystemStatus() {
        try {
            const resp = await fetch('/api/status');
            if (resp.ok) {
                const data = await resp.json();
                this.updateDashboard(data);
            }
        } catch (e) {
            console.error('Failed to fetch system status:', e);
        }
    }

    updateDashboard(data) {
        // Update service grid
        const serviceGrid = document.getElementById('service-grid');
        if (serviceGrid && data.services) {
            serviceGrid.innerHTML = '';
            for (const [name, status] of Object.entries(data.services)) {
                const shortName = name.replace('sentient-', '').replace('.service', '');
                const isActive = status === 'active';
                serviceGrid.innerHTML += `
                    <div class="service-item ${isActive ? 'active' : 'inactive'}">
                        <span class="service-dot"></span>
                        <span class="service-name">${shortName}</span>
                    </div>
                `;
            }
        }

        // Update system stats
        if (data.stats) {
            this.renderGauges(data.stats);
        }

        // Update network devices if present
        if (data.network_devices) {
            this.renderNetworkDevices(data.network_devices);
        }

        // Update weather widget
        if (data.weather && data.weather.temp) {
            this.renderWeather(data.weather);
        }

        // Update reminders widget
        this.renderReminders(data.reminders || []);

        // Update memory stats
        if (data.memory_stats) {
            this.renderMemoryStats(data.memory_stats);
        }

        // Update mood from status data
        if (data.mood && data.mood.emotion) {
            this.updateEmotion(data.mood.emotion, data.mood.intensity || 0.5);
        }
    }

    renderGauges(stats) {
        const container = document.getElementById('system-stats');
        if (!container || !stats) return;

        // Parse values from stats strings
        const gauges = [];

        // GPU gauge
        let gpuPct = 0, gpuLabel = '--';
        if (stats.gpu) {
            const match = stats.gpu.match(/(\d+)%/);
            if (match) gpuPct = parseInt(match[1]);
            gpuLabel = stats.gpu;
        }
        gauges.push({ label: 'GPU', percent: gpuPct, detail: gpuLabel, color: this._gaugeColor(gpuPct) });

        // RAM gauge
        let ramPct = 0, ramLabel = '--';
        if (stats.ram) {
            const match = stats.ram.match(/([\d.]+)G?\/([\d.]+)G?/);
            if (match) {
                ramPct = Math.round((parseFloat(match[1]) / parseFloat(match[2])) * 100);
            }
            ramLabel = stats.ram;
        }
        gauges.push({ label: 'RAM', percent: ramPct, detail: ramLabel, color: this._gaugeColor(ramPct) });

        // DISK gauge
        let diskPct = 0, diskLabel = '--';
        if (stats.disk) {
            const match = stats.disk.match(/(\d+)%/);
            if (match) diskPct = parseInt(match[1]);
            diskLabel = stats.disk;
        }
        gauges.push({ label: 'DISK', percent: diskPct, detail: diskLabel, color: this._gaugeColor(diskPct) });

        // Build gauge HTML
        container.innerHTML = '<div class="gauge-grid">' + gauges.map(g => this._buildGaugeSVG(g)).join('') + '</div>';

        // Uptime stays as text below gauges
        if (stats.uptime) {
            container.innerHTML += `<div class="stat-row uptime-row"><span class="stat-label">UPTIME</span><span class="stat-value">${this.escapeHtml(stats.uptime)}</span></div>`;
        }
    }

    _gaugeColor(percent) {
        if (percent <= 60) return '#00ff00';
        if (percent <= 80) return '#ffaa00';
        return '#ff0066';
    }

    _buildGaugeSVG({ label, percent, detail, color }) {
        const radius = 42;
        const circumference = 2 * Math.PI * radius;
        const offset = circumference * (1 - percent / 100);

        return `
            <div class="gauge-item">
                <svg class="gauge-svg" width="80" height="80" viewBox="0 0 100 100" style="--gauge-color: ${color}">
                    <defs>
                        <filter id="gauge-glow-${label}">
                            <feGaussianBlur stdDeviation="2" result="coloredBlur"/>
                            <feMerge>
                                <feMergeNode in="coloredBlur"/>
                                <feMergeNode in="SourceGraphic"/>
                            </feMerge>
                        </filter>
                    </defs>
                    <circle class="gauge-bg" cx="50" cy="50" r="${radius}"/>
                    <circle class="gauge-progress" cx="50" cy="50" r="${radius}"
                            stroke="${color}"
                            stroke-dasharray="${circumference}"
                            stroke-dashoffset="${offset}"
                            transform="rotate(-90 50 50)"
                            style="filter: url(#gauge-glow-${label})"/>
                    <text class="gauge-value" x="50" y="50" fill="${color}">${percent}%</text>
                </svg>
                <div class="gauge-label">${label}</div>
                <div class="gauge-detail">${this.escapeHtml(detail)}</div>
            </div>
        `;
    }

    renderNetworkDevices(devices) {
        let container = document.getElementById('network-devices');
        if (!container) {
            const section = document.createElement('div');
            section.className = 'dashboard-section';
            section.innerHTML = '<div class="section-title">NETWORK</div><div class="network-device-list" id="network-devices"></div>';
            // Insert after system stats section
            const statsSection = document.getElementById('system-stats')?.closest('.dashboard-section');
            if (statsSection) statsSection.after(section);
            container = document.getElementById('network-devices');
        }
        if (!container || !devices) return;

        container.innerHTML = `<div class="network-summary">${devices.length} device${devices.length !== 1 ? 's' : ''} on network</div>`;
        devices.forEach(d => {
            const isKnown = d.known || false;
            const name = d.name || d.hostname || d.mac || 'Unknown';
            const ip = d.ip || '';
            container.innerHTML += `
                <div class="network-device ${isKnown ? 'known' : 'unknown'}">
                    <span class="device-dot"></span>
                    <span class="device-name">${this.escapeHtml(name)}</span>
                    <span class="device-ip">${this.escapeHtml(ip)}</span>
                </div>`;
        });
    }

    renderWeather(weather) {
        let container = document.getElementById('weather-widget');
        if (!container) {
            const section = document.createElement('div');
            section.className = 'dashboard-section';
            section.innerHTML = '<div class="section-title">WEATHER</div><div class="weather-widget" id="weather-widget"></div>';
            const networkSection = document.getElementById('network-devices')?.closest('.dashboard-section');
            const insertAfter = networkSection || document.getElementById('system-stats')?.closest('.dashboard-section');
            if (insertAfter) insertAfter.after(section);
            container = document.getElementById('weather-widget');
        }
        if (!container) return;

        const temp = this.escapeHtml(weather.temp || '--');
        const condition = this.escapeHtml(weather.condition || '--');
        const humidity = weather.humidity ? this.escapeHtml(weather.humidity) : '';
        const wind = weather.wind ? this.escapeHtml(weather.wind) : '';

        let icon = '🌡';
        const condLower = (weather.condition || '').toLowerCase();
        if (condLower.includes('clear') || condLower.includes('sunny')) icon = '☀';
        else if (condLower.includes('cloud') || condLower.includes('overcast')) icon = '☁';
        else if (condLower.includes('rain') || condLower.includes('drizzle')) icon = '🌧';
        else if (condLower.includes('storm') || condLower.includes('thunder')) icon = '⛈';
        else if (condLower.includes('snow')) icon = '❄';
        else if (condLower.includes('fog') || condLower.includes('mist')) icon = '🌫';

        container.innerHTML = `
            <div class="weather-main">
                <span class="weather-icon">${icon}</span>
                <span class="weather-temp">${temp}</span>
            </div>
            <div class="weather-condition">${condition}</div>
            ${humidity || wind ? `<div class="weather-details">${humidity ? 'Humidity: ' + humidity : ''}${humidity && wind ? ' · ' : ''}${wind ? 'Wind: ' + wind : ''}</div>` : ''}
        `;
    }

    renderReminders(reminders) {
        let container = document.getElementById('reminders-widget');
        if (!container) {
            const section = document.createElement('div');
            section.className = 'dashboard-section';
            section.innerHTML = '<div class="section-title">REMINDERS</div><div class="reminders-widget" id="reminders-widget"></div>';
            const weatherSection = document.getElementById('weather-widget')?.closest('.dashboard-section');
            const insertAfter = weatherSection || document.getElementById('network-devices')?.closest('.dashboard-section') || document.getElementById('system-stats')?.closest('.dashboard-section');
            if (insertAfter) insertAfter.after(section);
            container = document.getElementById('reminders-widget');
        }
        if (!container) return;

        if (!reminders || reminders.length === 0) {
            container.innerHTML = '<div class="reminder-empty">No active reminders</div>';
            return;
        }

        container.innerHTML = reminders.map(r => {
            const remaining = r.remaining || 0;
            let timeStr;
            if (remaining > 3600) timeStr = Math.round(remaining / 3600) + 'h';
            else if (remaining > 60) timeStr = Math.round(remaining / 60) + 'm';
            else timeStr = Math.round(remaining) + 's';
            const isUrgent = remaining < 300;
            return `<div class="reminder-item ${isUrgent ? 'urgent' : ''}">
                <span class="reminder-text">${this.escapeHtml(r.text)}</span>
                <span class="reminder-time">${timeStr}</span>
            </div>`;
        }).join('');
    }

    renderMemoryStats(stats) {
        let container = document.getElementById('memory-count-widget');
        if (!container) {
            const section = document.getElementById('system-stats')?.closest('.dashboard-section');
            if (section) {
                const div = document.createElement('div');
                div.id = 'memory-count-widget';
                div.className = 'stat-row memory-row';
                section.appendChild(div);
            }
            container = document.getElementById('memory-count-widget');
        }
        if (!container) return;

        const totalMemories = stats.total_memories || stats.episodic_count || stats.total || 0;
        const coreCount = stats.core_count || stats.core_memory_count || 0;
        container.innerHTML = `<span class="stat-label">MEMORIES</span><span class="stat-value">${totalMemories}${coreCount ? ' (' + coreCount + ' core)' : ''}</span>`;
    }

    drawEmotionTrace() {
        const canvas = document.getElementById('emotion-canvas');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const w = canvas.width;
        const h = canvas.height;

        ctx.clearRect(0, 0, w, h);

        // Draw grid lines
        ctx.strokeStyle = 'rgba(0, 255, 255, 0.1)';
        ctx.lineWidth = 0.5;
        for (let y = 0; y < h; y += 15) {
            ctx.beginPath();
            ctx.moveTo(0, y);
            ctx.lineTo(w, y);
            ctx.stroke();
        }

        if (this.emotionHistory.length < 2) return;

        const recent = this.emotionHistory.slice(-30);
        const step = w / Math.max(recent.length - 1, 1);

        // Draw intensity line
        ctx.beginPath();
        ctx.strokeStyle = 'rgba(0, 255, 255, 0.8)';
        ctx.lineWidth = 1.5;

        recent.forEach((entry, i) => {
            const x = i * step;
            const y = h - (entry.intensity * h * 0.8) - (h * 0.1);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.stroke();

        // Draw colored dots
        recent.forEach((entry, i) => {
            const x = i * step;
            const y = h - (entry.intensity * h * 0.8) - (h * 0.1);
            ctx.beginPath();
            ctx.fillStyle = entry.color;
            ctx.arc(x, y, 2.5, 0, Math.PI * 2);
            ctx.fill();
        });
    }

    addActivityLogEntry(type, message) {
        const now = new Date();
        const timeStr = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;

        this.activityLog.push({ type, message, time: timeStr });
        if (this.activityLog.length > 30) this.activityLog.shift();

        const logEl = document.getElementById('activity-log');
        if (!logEl) return;

        const typeColors = {
            info: 'var(--color-primary)',
            success: 'var(--color-accent)',
            warning: 'var(--color-warning)',
            error: 'var(--color-danger)',
            emotion: 'var(--color-secondary)',
            thinking: 'var(--color-primary-dim)'
        };

        const entry = document.createElement('div');
        entry.className = 'log-entry';
        entry.innerHTML = `<span class="log-time">${timeStr}</span><span class="log-type" style="color:${typeColors[type] || typeColors.info}">${type.toUpperCase()}</span><span class="log-msg">${this.escapeHtml(message)}</span>`;

        logEl.appendChild(entry);

        // Keep only last 30
        while (logEl.children.length > 30) {
            logEl.removeChild(logEl.firstChild);
        }

        logEl.scrollTop = logEl.scrollHeight;
    }

    // ========================================================
    // CONNECTION STATUS
    // ========================================================

    updateConnectionStatus(status) {
        const statusDot = this.connectionStatus.querySelector('.status-dot');
        statusDot.classList.remove('status-connecting', 'status-connected', 'status-disconnected');

        switch (status) {
            case 'connecting':
                this.connectionStatus.innerHTML = '<span class="status-dot status-connecting"></span>CONNECTING';
                break;
            case 'connected':
                this.connectionStatus.innerHTML = '<span class="status-dot status-connected"></span>ONLINE';
                break;
            case 'disconnected':
            case 'error':
                this.connectionStatus.innerHTML = '<span class="status-dot status-disconnected"></span>OFFLINE';
                break;
        }

        // Dim avatar when disconnected
        const avatarCanvas = document.getElementById('avatar-canvas');
        if (avatarCanvas) {
            avatarCanvas.style.opacity = (status === 'connected') ? '1' : '0.3';
            avatarCanvas.style.transition = 'opacity 0.5s ease';
        }
    }

    scheduleReconnect() {
        this.clearReconnectTimer();
        this.reconnectTimer = setTimeout(() => {
            this.connectWebSocket();
        }, this.reconnectInterval);
    }

    clearReconnectTimer() {
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
    }

    // ========================================================
    // TIME UTILITIES
    // ========================================================

    startSystemClock() {
        const updateClock = () => {
            const now = new Date();
            this.systemTime.textContent =
                `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
        };
        updateClock();
        setInterval(updateClock, 1000);
    }

    getRelativeTime(date) {
        const now = new Date();
        const diffMs = now - date;
        const diffSec = Math.floor(diffMs / 1000);
        const diffMin = Math.floor(diffSec / 60);
        const diffHr = Math.floor(diffMin / 60);

        if (diffSec < 10) return 'just now';
        if (diffSec < 60) return `${diffSec}s ago`;
        if (diffMin < 60) return `${diffMin}m ago`;
        if (diffHr < 24) return `${diffHr}h ago`;
        return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
    }

    startRelativeTimeUpdater() {
        setInterval(() => {
            this.messageTimestamps.forEach((date, el) => {
                if (el.isConnected) {
                    el.textContent = this.getRelativeTime(date);
                } else {
                    this.messageTimestamps.delete(el);
                }
            });
        }, 30000); // Update every 30s
    }

    scrollToBottom() {
        requestAnimationFrame(() => {
            this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
        });
    }

    // ========================================================
    // CONVERSATION EXPORT
    // ========================================================

    exportConversation() {
        const messages = this.messagesContainer.querySelectorAll('.message');
        if (messages.length === 0) {
            this.showNotification('No messages to export', 'warning');
            return;
        }

        let markdown = `# Cortana Conversation\n`;
        markdown += `**Exported:** ${new Date().toLocaleString()}\n`;
        markdown += `**System:** Sentient Core v2 / Jetson Orin Nano\n\n---\n\n`;

        messages.forEach(msg => {
            const isUser = msg.classList.contains('user');
            const bubble = msg.querySelector('.message-bubble');
            if (!bubble) return;
            const text = bubble.textContent || bubble.innerText;
            const timeEl = msg.querySelector('.message-timestamp');
            const time = timeEl ? timeEl.textContent : '';

            if (isUser) {
                markdown += `### You ${time ? '(' + time + ')' : ''}\n${text}\n\n`;
            } else {
                markdown += `### Cortana ${time ? '(' + time + ')' : ''}\n${text}\n\n`;
            }
        });

        markdown += `---\n*Exported from Sentient Core Neural Interface*\n`;

        const blob = new Blob([markdown], { type: 'text/markdown' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `cortana-chat-${new Date().toISOString().slice(0,10)}.md`;
        a.click();
        URL.revokeObjectURL(url);
        this.showNotification('Conversation exported', 'success', 2000);
    }

    // ========================================================
    // KEYBOARD SHORTCUTS HELP
    // ========================================================

    showShortcutsOverlay() {
        // Remove existing overlay if any
        const existing = document.getElementById('shortcuts-overlay');
        if (existing) { existing.remove(); return; }

        const overlay = document.createElement('div');
        overlay.id = 'shortcuts-overlay';
        overlay.style.cssText = `
            position: fixed; inset: 0; background: rgba(0,0,0,0.85);
            display: flex; align-items: center; justify-content: center;
            z-index: 10000; backdrop-filter: blur(4px);
        `;

        const card = document.createElement('div');
        card.style.cssText = `
            background: #0f0f0f; border: 1px solid #00cccc; border-radius: 4px;
            padding: 24px 32px; max-width: 420px; width: 90%;
            font-family: 'Share Tech Mono', monospace; color: #fff;
            box-shadow: 0 0 30px rgba(0,255,255,0.15);
        `;
        card.innerHTML = `
            <h3 style="color:#00ffff; margin-bottom:16px; font-size:1rem; letter-spacing:2px;">
                KEYBOARD SHORTCUTS
            </h3>
            <div style="display:grid; grid-template-columns:auto 1fr; gap:8px 16px; font-size:0.85rem;">
                <kbd style="color:#00ffff">Enter</kbd><span>Send message</span>
                <kbd style="color:#00ffff">Shift+Enter</kbd><span>New line</span>
                <kbd style="color:#00ffff">Escape</kbd><span>Close panel / Clear input</span>
                <kbd style="color:#00ffff">Ctrl+D</kbd><span>Toggle neural dashboard</span>
                <kbd style="color:#00ffff">Ctrl+E</kbd><span>Export conversation</span>
                <kbd style="color:#00ffff">Ctrl+F</kbd><span>Search messages</span>
                <kbd style="color:#00ffff">?</kbd><span>Show this help</span>
            </div>
            <p style="margin-top:16px; color:#555; font-size:0.75rem; text-align:center;">
                Press Escape or click outside to close
            </p>
        `;

        overlay.appendChild(card);
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) overlay.remove();
        });

        document.body.appendChild(overlay);
    }

    toggleMessageSearch() {
        let searchBar = document.getElementById('message-search');
        if (searchBar) {
            searchBar.remove();
            // Show all messages
            document.querySelectorAll('.message').forEach(m => m.style.display = '');
            return;
        }
        searchBar = document.createElement('div');
        searchBar.id = 'message-search';
        searchBar.className = 'message-search';
        searchBar.innerHTML = `
            <input type="text" id="search-input" placeholder="Search messages..." autocomplete="off">
            <button id="search-close" class="search-close">&times;</button>
        `;
        const messagesEl = document.getElementById('messages');
        messagesEl.parentElement.insertBefore(searchBar, messagesEl);

        const input = document.getElementById('search-input');
        const closeBtn = document.getElementById('search-close');
        input.focus();

        input.addEventListener('input', () => {
            const query = input.value.toLowerCase();
            document.querySelectorAll('.message').forEach(m => {
                const text = m.textContent.toLowerCase();
                m.style.display = query && !text.includes(query) ? 'none' : '';
            });
        });

        closeBtn.addEventListener('click', () => this.toggleMessageSearch());
    }

    // Keep-alive ping
    startKeepAlive() {
        setInterval(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({ type: 'ping' }));
            }
        }, 30000);
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.chatInterface = new ChatInterface();
    window.chatInterface.startKeepAlive();

    // Auto-report avatar diagnostics when model loads
    window.onAvatarDiagnostic = (diag) => {
        console.log('Avatar diagnostic:', diag);
        const ci = window.chatInterface;
        if (ci && ci.ws && ci.ws.readyState === WebSocket.OPEN) {
            ci.ws.send(JSON.stringify({
                type: 'diagnostic',
                data: diag
            }));
        }
    };

    // Fallback diagnostic
    window._diagFallbackTimer = setTimeout(() => {
        const ci = window.chatInterface;
        if (ci && ci.ws && ci.ws.readyState === WebSocket.OPEN) {
            const pageDiag = {
                source: 'app.js_fallback',
                avatarRendererExists: !!window.avatarRenderer,
                avatarCanvasExists: !!document.getElementById('avatar-canvas'),
                canvasSize: null,
                userAgent: navigator.userAgent,
                pageVersion: '20260217g',
                errors: window._jsErrors || []
            };
            const canvas = document.getElementById('avatar-canvas');
            if (canvas) {
                pageDiag.canvasSize = { w: canvas.clientWidth, h: canvas.clientHeight };
            }
            if (!window.avatarRenderer) {
                ci.ws.send(JSON.stringify({
                    type: 'diagnostic',
                    data: pageDiag
                }));
            }
        }
    }, 10000);
});

// Capture JS errors
window._jsErrors = [];
window.addEventListener('error', (e) => {
    window._jsErrors.push({
        message: e.message,
        filename: e.filename,
        lineno: e.lineno,
        colno: e.colno,
        time: Date.now()
    });
    const ci = window.chatInterface;
    if (ci && ci.ws && ci.ws.readyState === WebSocket.OPEN) {
        ci.ws.send(JSON.stringify({
            type: 'diagnostic',
            data: { source: 'js_error', message: e.message, filename: e.filename, lineno: e.lineno }
        }));
    }
});

// Handle visibility change
document.addEventListener('visibilitychange', () => {
    if (!document.hidden && window.chatInterface) {
        if (!window.chatInterface.ws || window.chatInterface.ws.readyState !== WebSocket.OPEN) {
            window.chatInterface.connectWebSocket();
        }
    }
});
