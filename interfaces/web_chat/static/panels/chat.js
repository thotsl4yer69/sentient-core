/**
 * SENTIENT CORE - CHAT PANEL
 * ES module. Handles messages, streaming, input, voice, TTS, commands,
 * contemplation, feedback, search, and export for the dashboard v3.
 */

import {
  renderMarkdown,
  escapeHtml,
  getRelativeTime,
  playSound,
  showToast,
  trackTimestamp
} from '../utils.js';

// ---------------------------------------------------------------------------
// SVG icons
// ---------------------------------------------------------------------------

const userSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="20" height="20"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
const cortanaSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="20" height="20"><polygon points="12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5"/><circle cx="12" cy="12" r="3"/></svg>';

const copySvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const checkSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><polyline points="20 6 9 17 4 12"/></svg>';

const thumbUpSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
const thumbDownSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg>';

// ---------------------------------------------------------------------------
// Proactive label map
// ---------------------------------------------------------------------------

const PROACTIVE_LABELS = {
  boot: 'SYSTEM ONLINE',
  boredom: 'INITIATED CONTACT',
  concern: 'SECURITY ALERT',
  curiosity: 'OBSERVATION',
  care: 'CHECK-IN',
  excitement: 'SYSTEM EVENT',
  system_observation: 'SYSTEM MONITOR',
  idle_thought: 'IDLE THOUGHT',
  reminder: 'REMINDER',
  daily_briefing: 'DAILY BRIEFING',
  network_event: 'NETWORK ALERT',
  memory_followup: 'MEMORY RECALL',
  night_owl: 'NIGHT OWL',
  streak_tracker: 'STREAK',
  conversation_recap: 'RECAP',
  learning_moment: 'CURIOUS',
  weather_alert: 'WEATHER',
  first_morning_greeting: 'GOOD MORNING'
};

// ---------------------------------------------------------------------------
// Module-private state
// ---------------------------------------------------------------------------

let _ws = null;
let _ttsEnabled = false;
let _handsFreeMode = false;
let _isListening = false;
let _recognition = null;
let _selectedVoice = null;
let _isSpeaking = false;
let _piperAudioPlayed = false;
let _streamingEl = null;
let _streamingText = '';
let _unreadCount = 0;
let _userAtBottom = true;
let _messageQueue = [];
let _deferredInstallPrompt = null;
let _scrollBtn = null;

// DOM refs — populated in init()
let _messages = null;
let _messageInput = null;
let _sendBtn = null;
let _voiceBtn = null;
let _ttsBtn = null;
let _handsFreeBtn = null;
let _thinkingIndicator = null;
let _thinkingStage = null;
let _charCount = null;
let _audioPlayer = null;
let _commandPalette = null;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export function init() {
  _messages         = document.getElementById('messages');
  _messageInput     = document.getElementById('message-input');
  _sendBtn          = document.getElementById('send-btn');
  _voiceBtn         = document.getElementById('voice-btn');
  _ttsBtn           = document.getElementById('tts-btn');
  _handsFreeBtn     = document.getElementById('handsfree-btn');
  _thinkingIndicator = document.getElementById('thinking-indicator');
  _thinkingStage    = document.getElementById('thinking-stage');
  _charCount        = document.getElementById('char-count');
  _audioPlayer      = document.getElementById('audio-player');

  _initCommandPalette();
  _setupEventListeners();
  _setupScrollDetection();
  _createScrollButton();
  _initVoiceRecognition();
  _initBrowserTTS();
  _initInstallPrompt();
}

export function setWebSocket(ws) {
  _ws = ws;
  // Flush any queued messages
  if (_ws && _ws.readyState === WebSocket.OPEN) {
    _processMessageQueue();
  }
}

export function sendMessage() {
  const text = _messageInput ? _messageInput.value.trim() : '';
  if (!text) return;

  if (text.length > 2000) {
    showToast('Message too long (max 2000 characters)', 'error');
    return;
  }

  const message = { type: 'message', text };

  if (_ws && _ws.readyState === WebSocket.OPEN) {
    _ws.send(JSON.stringify(message));
    playSound('send');
    _messageInput.value = '';
    if (_charCount) _charCount.textContent = '0';
    _autoResizeTextarea();
    _messageInput.focus();

    if (window.avatarRenderer?.updateFromChatMessage) {
      window.avatarRenderer.updateFromChatMessage('user', text);
    }
  } else {
    _messageQueue.push(message);
    showToast('Message queued — reconnecting...', 'warning');
  }
}

export function addMessage(data) {
  const { role, content, timestamp, emotion } = data;
  const isProactive = data.proactive === true;
  const suggestions = data.suggestions || null;
  const triggerType = data.trigger_type || 'observation';

  // Remove streaming placeholder if final assistant message arrives
  if (role === 'assistant') {
    const existing = document.getElementById('streaming-message');
    if (existing) existing.remove();
    _streamingEl = null;
    _streamingText = '';
  }

  const { messageEl } = _createMessageElement(role, content, timestamp, emotion, false, suggestions, isProactive);

  // Proactive visual indicator
  if (isProactive) {
    messageEl.classList.add('proactive');
    const labelText = PROACTIVE_LABELS[triggerType] || 'AUTONOMOUS';
    const label = document.createElement('div');
    label.className = 'proactive-label';
    label.textContent = labelText;
    const bubble = messageEl.querySelector('.message-bubble');
    if (bubble) bubble.prepend(label);
  }

  _messages.appendChild(messageEl);

  if (role === 'assistant') {
    playSound(isProactive ? 'proactive' : 'message');
    if (!_userAtBottom) {
      _unreadCount++;
      _showScrollBtn();
    }
    showThinking(false);

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

    _piperAudioPlayed = false;
  }

  if (_userAtBottom) scrollToBottom();
}

export function handleStream(data) {
  const { token, done } = data;

  if (done) {
    if (_streamingEl && _streamingText) {
      _streamingEl.innerHTML = renderMarkdown(_streamingText);
      _streamingEl.querySelectorAll('pre code').forEach(block => {
        if (typeof hljs !== 'undefined') hljs.highlightElement(block);
      });

      // Add copy button to completed streaming message
      const streamMsg = document.getElementById('streaming-message');
      if (streamMsg) {
        const metaEl = streamMsg.querySelector('.message-meta');
        if (metaEl && !metaEl.querySelector('.btn-copy')) {
          const capturedText = _streamingText;
          metaEl.appendChild(_makeCopyBtn(() => capturedText));
        }

        // Add code copy buttons
        requestAnimationFrame(() => {
          streamMsg.querySelectorAll('pre code').forEach(block => {
            _addCodeCopyBtn(block);
          });
        });
      }
    }

    if (window.avatarRenderer?.setState) window.avatarRenderer.setState('idle');
    _streamingEl = null;
    _piperAudioPlayed = false;

    if (window.neuralPulse) window.neuralPulse();
    return;
  }

  if (!token) return;

  if (!_streamingEl) {
    showThinking(false);
    const { messageEl, bubbleEl } = _createMessageElement('assistant', '', null, null, true);
    _messages.appendChild(messageEl);
    _streamingEl = bubbleEl;
    _streamingText = '';
    if (window.avatarRenderer?.setState) window.avatarRenderer.setState('processing');
  }

  if (window.avatarRenderer?.onStreamToken) window.avatarRenderer.onStreamToken();
  _streamingText += token;

  // Plain text during streaming with blinking cursor
  _streamingEl.textContent = _streamingText;
  let cursor = _streamingEl.querySelector('.streaming-cursor');
  if (!cursor) {
    cursor = document.createElement('span');
    cursor.className = 'streaming-cursor';
  }
  _streamingEl.appendChild(cursor);

  if (_userAtBottom) scrollToBottom();
}

export function showThinking(active, stage = '') {
  if (!_thinkingIndicator) return;

  if (active) {
    _thinkingIndicator.style.display = 'block';
    if (_thinkingStage) _thinkingStage.textContent = stage ? `// ${stage}` : '';

    if (window.avatarRenderer) {
      if (typeof window.avatarRenderer.setEmotion === 'function') {
        window.avatarRenderer.setEmotion('thinking', 0.6);
      }
      if (typeof window.avatarRenderer.setAttentionState === 'function') {
        window.avatarRenderer.setAttentionState('focused');
      }
    }
  } else {
    _thinkingIndicator.style.display = 'none';
  }

  scrollToBottom();
}

export function showWelcome(data) {
  const text = data.text || 'Neural link established. How can I help?';
  const services = data.services || {};
  const stats = data.stats || {};

  const messageEl = document.createElement('div');
  messageEl.className = 'message assistant welcome-message';

  const avatarEl = document.createElement('div');
  avatarEl.className = 'message-avatar';
  avatarEl.innerHTML = cortanaSvg;

  const contentEl = document.createElement('div');
  contentEl.className = 'message-content';

  const bubbleEl = document.createElement('div');
  bubbleEl.className = 'message-bubble';
  bubbleEl.innerHTML = renderMarkdown(text);

  if (Object.keys(services).length > 0) {
    const statusBar = document.createElement('div');
    statusBar.className = 'welcome-status-bar';
    const activeCount = Object.values(services).filter(s => s === 'active').length;
    const total = Object.keys(services).length;
    statusBar.innerHTML = `<span class="welcome-stat"><span class="stat-dot stat-dot-ok"></span>${activeCount}/${total} systems online</span>`;
    if (stats.gpu) {
      statusBar.innerHTML += `<span class="welcome-stat"><span class="stat-dot stat-dot-ok"></span>GPU ${escapeHtml(stats.gpu)}</span>`;
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

  _messages.appendChild(messageEl);
  scrollToBottom();

  if (data.mood?.emotion && window.avatarRenderer?.setEmotion) {
    window.avatarRenderer.setEmotion(data.mood.emotion, data.mood.intensity || 0.5);
  }
}

export function showContemplation() {
  const existing = document.getElementById('contemplation-panel');
  if (existing) existing.remove();

  const panel = document.createElement('div');
  panel.id = 'contemplation-panel';
  panel.className = 'contemplation-panel';
  panel.innerHTML = `
    <div class="contemplation-header">
      <span class="contemplation-icon">&#9672;</span>
      <span>DEEP CONTEMPLATION ACTIVE</span>
      <span class="contemplation-spinner"></span>
    </div>
    <div class="contemplation-voices" id="contemplation-voices">
      <div class="voice-slot" data-voice="observer"><span class="voice-label">OBSERVER</span><span class="voice-status">analyzing...</span></div>
      <div class="voice-slot" data-voice="analyst"><span class="voice-label">ANALYST</span><span class="voice-status">reasoning...</span></div>
      <div class="voice-slot" data-voice="empath"><span class="voice-label">EMPATH</span><span class="voice-status">feeling...</span></div>
      <div class="voice-slot" data-voice="skeptic"><span class="voice-label">SKEPTIC</span><span class="voice-status">questioning...</span></div>
      <div class="voice-slot" data-voice="memory"><span class="voice-label">MEMORY</span><span class="voice-status">connecting...</span></div>
    </div>
    <div class="contemplation-footer">Synthesizing perspectives...</div>
  `;

  _messages.appendChild(panel);
  scrollToBottom();

  // Animate voices in one by one
  const slots = panel.querySelectorAll('.voice-slot');
  slots.forEach((slot, i) => {
    slot.style.opacity = '0';
    slot.style.transform = 'translateX(-20px)';
    setTimeout(() => {
      slot.style.transition = 'all 0.4s ease-out';
      slot.style.opacity = '1';
      slot.style.transform = 'translateX(0)';
    }, 500 + i * 600);
  });
}

export function populateContemplation(data) {
  const panel = document.getElementById('contemplation-panel');
  if (!panel) return;

  const voices = data.voices || {};
  const voicesContainer = document.getElementById('contemplation-voices');
  if (!voicesContainer) return;

  for (const [voiceName, voiceData] of Object.entries(voices)) {
    const slot = voicesContainer.querySelector(`[data-voice="${voiceName}"]`);
    if (slot) {
      const content = voiceData.content || '';
      const truncated = content.length > 120 ? content.substring(0, 120) + '...' : content;
      slot.querySelector('.voice-status').textContent = truncated;
      slot.classList.add('voice-complete');
      slot.title = content;
    }
  }

  const footer = panel.querySelector('.contemplation-footer');
  if (footer) {
    const totalTime = ((data.total_time_ms || 0) / 1000).toFixed(1);
    footer.textContent = `Synthesis complete (${totalTime}s) — response incoming`;
    footer.classList.add('synthesis-complete');
  }

  // Auto-collapse after 8s with click-to-expand
  setTimeout(() => {
    if (panel && panel.isConnected) {
      panel.classList.add('contemplation-collapsed');
      panel.addEventListener('click', () => {
        panel.classList.toggle('contemplation-collapsed');
      }, { once: false });
    }
  }, 8000);
}

export function loadHistory(messages) {
  if (!_messages) return;
  _messages.innerHTML = '';
  messages.forEach(msg => addMessage(msg));
}

export function scrollToBottom() {
  if (!_messages) return;
  requestAnimationFrame(() => {
    _messages.scrollTop = _messages.scrollHeight;
  });
}

// Called by app.js when a tts_audio WS message arrives
export function playTTSAudio(audioBase64, format, phonemes, duration) {
  if (!_ttsEnabled || !audioBase64) return;
  _piperAudioPlayed = true;

  try {
    const byteCharacters = atob(audioBase64);
    const byteArray = new Uint8Array(byteCharacters.length);
    for (let i = 0; i < byteCharacters.length; i++) {
      byteArray[i] = byteCharacters.charCodeAt(i);
    }
    const blob = new Blob([byteArray], { type: `audio/${format || 'wav'}` });
    const audioUrl = URL.createObjectURL(blob);

    if (!_audioPlayer) return;
    _audioPlayer.src = audioUrl;

    if (_ttsBtn) _ttsBtn.classList.add('playing');

    _audioPlayer.onended = () => {
      URL.revokeObjectURL(audioUrl);
      if (_ttsBtn) _ttsBtn.classList.remove('playing');
      if (window.avatarRenderer) window.avatarRenderer.setSpeaking(false);
      if (_handsFreeMode) {
        setTimeout(() => _startListening(), 400);
      }
    };

    _audioPlayer.onerror = () => {
      URL.revokeObjectURL(audioUrl);
      if (_ttsBtn) _ttsBtn.classList.remove('playing');
      if (window.avatarRenderer) window.avatarRenderer.setSpeaking(false);
      if (_handsFreeMode) {
        setTimeout(() => _startListening(), 400);
      }
    };

    if (phonemes && phonemes.length > 0 && window.avatarRenderer?.processPhonemes) {
      window.avatarRenderer.processPhonemes({ phonemes, duration });
    }

    _audioPlayer.play().catch(() => {
      if (_ttsBtn) _ttsBtn.classList.remove('playing');
      if (window.avatarRenderer) window.avatarRenderer.setSpeaking(false);
    });
  } catch (err) {
    console.error('[TTS] Audio decode error:', err);
  }
}

// ---------------------------------------------------------------------------
// Message element construction
// ---------------------------------------------------------------------------

function _createMessageElement(role, content, timestamp, emotion, isStreaming = false, suggestions = null, isProactive = false) {
  const messageEl = document.createElement('div');
  messageEl.className = `message ${role}`;
  if (isStreaming) messageEl.id = 'streaming-message';

  const avatarEl = document.createElement('div');
  avatarEl.className = 'message-avatar';
  avatarEl.innerHTML = role === 'user' ? userSvg : cortanaSvg;

  const contentEl = document.createElement('div');
  contentEl.className = 'message-content';

  const bubbleEl = document.createElement('div');
  bubbleEl.className = 'message-bubble';

  if (role === 'assistant' && content && !isStreaming) {
    bubbleEl.innerHTML = renderMarkdown(content);
    bubbleEl.querySelectorAll('pre code').forEach(block => {
      if (typeof hljs !== 'undefined') hljs.highlightElement(block);
    });
  } else {
    bubbleEl.textContent = content || '';
  }

  // Meta row
  const metaEl = document.createElement('div');
  metaEl.className = 'message-meta';

  const ts = timestamp ? new Date(timestamp) : new Date();
  const timeEl = document.createElement('span');
  timeEl.className = 'message-timestamp';
  timeEl.textContent = getRelativeTime(ts);
  timeEl.dataset.timestamp = ts.getTime();
  trackTimestamp(timeEl, ts);
  metaEl.appendChild(timeEl);

  // Copy button for completed assistant messages
  if (role === 'assistant' && content && !isStreaming) {
    const capturedContent = content;
    metaEl.appendChild(_makeCopyBtn(() => capturedContent));
  }

  contentEl.appendChild(bubbleEl);
  contentEl.appendChild(metaEl);

  // Feedback buttons
  if (role === 'assistant' && content && !isStreaming) {
    const feedbackDiv = document.createElement('div');
    feedbackDiv.className = 'message-feedback';

    const upBtn = document.createElement('button');
    upBtn.className = 'feedback-btn feedback-up';
    upBtn.title = 'Good response';
    upBtn.dataset.feedback = 'up';
    upBtn.innerHTML = thumbUpSvg;
    upBtn.addEventListener('click', (e) => _sendFeedback(e, 'up', content.substring(0, 100)));

    const downBtn = document.createElement('button');
    downBtn.className = 'feedback-btn feedback-down';
    downBtn.title = 'Could be better';
    downBtn.dataset.feedback = 'down';
    downBtn.innerHTML = thumbDownSvg;
    downBtn.addEventListener('click', (e) => _sendFeedback(e, 'down', content.substring(0, 100)));

    feedbackDiv.appendChild(upBtn);
    feedbackDiv.appendChild(downBtn);
    contentEl.appendChild(feedbackDiv);
  }

  // Suggestion chips
  if (role === 'assistant' && !isStreaming && !isProactive && suggestions && suggestions.length > 0) {
    const suggestionsDiv = document.createElement('div');
    suggestionsDiv.className = 'suggestion-chips';
    suggestions.forEach(chipText => {
      const chip = document.createElement('button');
      chip.className = 'suggestion-chip';
      chip.textContent = chipText;
      chip.addEventListener('click', () => {
        if (_messageInput) _messageInput.value = chipText;
        sendMessage();
        suggestionsDiv.remove();
      });
      suggestionsDiv.appendChild(chip);
    });
    contentEl.appendChild(suggestionsDiv);
  }

  messageEl.appendChild(avatarEl);
  messageEl.appendChild(contentEl);

  // Code copy buttons after element is in DOM
  if (role === 'assistant' && content && !isStreaming) {
    requestAnimationFrame(() => {
      messageEl.querySelectorAll('pre code').forEach(block => {
        _addCodeCopyBtn(block);
      });
    });
  }

  return { messageEl, bubbleEl };
}

function _makeCopyBtn(getTextFn) {
  const btn = document.createElement('button');
  btn.className = 'btn-copy';
  btn.title = 'Copy message';
  btn.innerHTML = copySvg;
  btn.addEventListener('click', () => {
    navigator.clipboard.writeText(getTextFn()).then(() => {
      btn.innerHTML = checkSvg;
      setTimeout(() => { btn.innerHTML = copySvg; }, 1500);
    });
  });
  return btn;
}

function _addCodeCopyBtn(block) {
  const pre = block.parentElement;
  if (!pre || pre.querySelector('.code-copy-btn')) return;
  pre.style.position = 'relative';
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
  pre.appendChild(btn);
}

// ---------------------------------------------------------------------------
// Scroll management
// ---------------------------------------------------------------------------

function _createScrollButton() {
  const btn = document.createElement('button');
  btn.className = 'scroll-to-bottom';
  btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg><span class="unread-badge" style="display:none">0</span>';
  btn.addEventListener('click', () => {
    scrollToBottom();
    _unreadCount = 0;
    const badge = btn.querySelector('.unread-badge');
    if (badge) badge.style.display = 'none';
    btn.classList.remove('visible');
  });

  // Append to panel body, or fallback to body
  const body = document.getElementById('panel-chat-body') || document.body;
  body.appendChild(btn);
  _scrollBtn = btn;
}

function _setupScrollDetection() {
  if (!_messages) return;
  _messages.addEventListener('scroll', () => {
    const { scrollTop, scrollHeight, clientHeight } = _messages;
    _userAtBottom = (scrollHeight - scrollTop - clientHeight) < 60;

    if (_userAtBottom) {
      _unreadCount = 0;
      if (_scrollBtn) {
        _scrollBtn.classList.remove('visible');
        const badge = _scrollBtn.querySelector('.unread-badge');
        if (badge) badge.style.display = 'none';
      }
    }
  });
}

function _showScrollBtn() {
  if (!_userAtBottom && _scrollBtn) {
    _scrollBtn.classList.add('visible');
    if (_unreadCount > 0) {
      const badge = _scrollBtn.querySelector('.unread-badge');
      if (badge) {
        badge.textContent = _unreadCount;
        badge.style.display = 'flex';
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------

function _setupEventListeners() {
  if (_sendBtn) {
    _sendBtn.addEventListener('click', () => sendMessage());
  }

  if (_messageInput) {
    _messageInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    _messageInput.addEventListener('input', () => {
      if (_charCount) _charCount.textContent = _messageInput.value.length;
      _autoResizeTextarea();

      // Command palette trigger
      if (_messageInput.value === '/') {
        _showCommandPalette();
      } else {
        _hideCommandPalette();
      }
    });
  }

  if (_voiceBtn) _voiceBtn.addEventListener('click', () => _toggleVoiceInput());
  if (_ttsBtn) _ttsBtn.addEventListener('click', () => _toggleTTS());
  if (_handsFreeBtn) _handsFreeBtn.addEventListener('click', () => _toggleHandsFree());

  // Global keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const searchBar = document.getElementById('message-search');
      if (searchBar) {
        _closeMessageSearch();
        return;
      }
      if (_messageInput && _messageInput.value) {
        _messageInput.value = '';
        if (_charCount) _charCount.textContent = '0';
        _autoResizeTextarea();
      }
    }

    if (e.ctrlKey && e.key === 'e') {
      e.preventDefault();
      _exportConversation();
      return;
    }

    if (e.ctrlKey && e.key === 'f') {
      e.preventDefault();
      _toggleMessageSearch();
      return;
    }

    if (e.key === '?' && document.activeElement !== _messageInput) {
      e.preventDefault();
      _showShortcutsOverlay();
    }
  });

  // Prevent accidental unload
  window.addEventListener('beforeunload', (e) => {
    if (_messageInput && _messageInput.value.trim().length > 0) {
      e.preventDefault();
      e.returnValue = '';
    }
  });
}

function _autoResizeTextarea() {
  if (!_messageInput) return;
  _messageInput.style.height = 'auto';
  _messageInput.style.height = Math.min(_messageInput.scrollHeight, 200) + 'px';
}

// ---------------------------------------------------------------------------
// Feedback
// ---------------------------------------------------------------------------

function _sendFeedback(event, type, snippet) {
  if (_ws && _ws.readyState === WebSocket.OPEN) {
    _ws.send(JSON.stringify({
      type: 'feedback',
      feedback: type,
      snippet,
      timestamp: Date.now() / 1000
    }));
  }
  const btn = event.target.closest('.feedback-btn');
  if (btn) btn.classList.add('feedback-sent');
  showToast(type === 'up' ? 'Thanks! Noted.' : "Got it, I'll adjust.", 'info');
}

// ---------------------------------------------------------------------------
// Message queue
// ---------------------------------------------------------------------------

function _processMessageQueue() {
  while (_messageQueue.length > 0) {
    const msg = _messageQueue.shift();
    if (_ws && _ws.readyState === WebSocket.OPEN) {
      _ws.send(JSON.stringify(msg));
    }
  }
}

// ---------------------------------------------------------------------------
// Command palette
// ---------------------------------------------------------------------------

const COMMANDS = [
  { icon: '&#9881;', label: 'Diagnostic', cmd: 'run a full diagnostic' },
  { icon: '&#9732;', label: 'Network',    cmd: 'show network status' },
  { icon: '&#9889;', label: 'Status',     cmd: "what's your current status?" },
  { icon: '&#9829;', label: 'Mood',       cmd: 'how are you feeling right now?' },
  { icon: '&#9635;', label: 'Services',   cmd: 'show me the service status' },
  { icon: '&#9733;', label: 'Memory',     cmd: 'what do you remember about me?' },
  { icon: '&#8982;', label: 'Scan',       cmd: 'run a network scan' },
  { icon: '&#9776;', label: 'Logs',       cmd: 'check the system logs' }
];

function _initCommandPalette() {
  const palette = document.createElement('div');
  palette.className = 'command-palette';
  palette.id = 'command-palette';
  palette.style.display = 'none';

  const gridHtml = COMMANDS.map(c =>
    `<button class="palette-cmd" data-cmd="${escapeHtml(c.cmd)}">
      <span class="palette-icon">${c.icon}</span>
      <span class="palette-label">${escapeHtml(c.label)}</span>
    </button>`
  ).join('');

  palette.innerHTML = `<div class="palette-header">QUICK COMMANDS</div><div class="palette-grid">${gridHtml}</div>`;

  const chatBody = document.getElementById('panel-chat-body');
  if (chatBody) {
    chatBody.appendChild(palette);
  } else {
    // Fallback: insert before messages container's parent
    const messagesEl = document.getElementById('messages');
    if (messagesEl && messagesEl.parentElement) {
      messagesEl.parentElement.insertBefore(palette, messagesEl);
    }
  }

  _commandPalette = palette;

  palette.querySelectorAll('.palette-cmd').forEach(btn => {
    btn.addEventListener('click', () => {
      const cmd = btn.dataset.cmd;
      if (_messageInput) _messageInput.value = cmd;
      _hideCommandPalette();
      sendMessage();
    });
  });
}

function _showCommandPalette() {
  if (_commandPalette) {
    _commandPalette.style.display = 'block';
    _commandPalette.classList.add('palette-visible');
  }
}

function _hideCommandPalette() {
  if (_commandPalette) {
    _commandPalette.classList.remove('palette-visible');
    setTimeout(() => { if (_commandPalette) _commandPalette.style.display = 'none'; }, 200);
  }
}

// ---------------------------------------------------------------------------
// Voice recognition
// ---------------------------------------------------------------------------

function _initVoiceRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    console.warn('[Voice] Speech recognition not supported');
    if (_voiceBtn) _voiceBtn.style.display = 'none';
    return;
  }

  _recognition = new SR();
  _recognition.continuous = false;
  _recognition.interimResults = true;
  _recognition.lang = 'en-AU';
  _recognition.maxAlternatives = 1;

  _recognition.onstart = () => {
    _isListening = true;
    if (_voiceBtn) _voiceBtn.classList.add('listening');
    if (_messageInput) _messageInput.placeholder = 'LISTENING...';
  };

  _recognition.onresult = (event) => {
    let finalTranscript = '';
    let interimTranscript = '';

    for (let i = event.resultIndex; i < event.results.length; i++) {
      if (event.results[i].isFinal) {
        finalTranscript += event.results[i][0].transcript;
      } else {
        interimTranscript += event.results[i][0].transcript;
      }
    }

    if (_messageInput) {
      _messageInput.value = finalTranscript || interimTranscript;
      _messageInput.dispatchEvent(new Event('input'));
    }

    if (finalTranscript) {
      setTimeout(() => { if (_sendBtn) _sendBtn.click(); }, 300);
    }
  };

  _recognition.onerror = (event) => {
    console.warn('[Voice] Error:', event.error);
    _stopListening();
    if (event.error === 'not-allowed') {
      showToast('Microphone access denied. Check browser permissions.', 'error');
    } else if (event.error === 'no-speech') {
      showToast('No speech detected. Try again.', 'warning');
    }
  };

  _recognition.onend = () => {
    _stopListening();
  };
}

function _startListening() {
  if (!_recognition) return;
  try { _recognition.start(); } catch (e) { console.warn('[Voice] Start error:', e); }
}

function _stopListening() {
  _isListening = false;
  if (_voiceBtn) _voiceBtn.classList.remove('listening');
  if (_messageInput && _messageInput.placeholder === 'LISTENING...') {
    _messageInput.placeholder = 'TRANSMIT MESSAGE TO CORTANA...';
  }
  try { if (_recognition) _recognition.stop(); } catch (_) {}
}

function _toggleVoiceInput() {
  if (_isListening) { _stopListening(); } else { _startListening(); }
}

// ---------------------------------------------------------------------------
// TTS
// ---------------------------------------------------------------------------

function _initBrowserTTS() {
  if (!('speechSynthesis' in window)) {
    console.warn('[TTS] Browser speech synthesis not supported');
    return;
  }

  const loadVoices = () => {
    const voices = speechSynthesis.getVoices();
    if (voices.length === 0) return;
    _selectedVoice =
      voices.find(v => v.lang === 'en-AU' && v.name.toLowerCase().includes('female')) ||
      voices.find(v => v.lang.startsWith('en') && v.name.toLowerCase().includes('female')) ||
      voices.find(v => v.lang.startsWith('en') && /Samantha|Karen|Moira/.test(v.name)) ||
      voices.find(v => v.lang.startsWith('en'));
  };

  loadVoices();
  speechSynthesis.onvoiceschanged = loadVoices;
}

function _speakText(text) {
  if (!('speechSynthesis' in window) || !_ttsEnabled) return;

  speechSynthesis.cancel();

  const clean = text
    .replace(/```[\s\S]*?```/g, 'code block')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/#{1,6}\s/g, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/[*_~`#>-]/g, '')
    .trim();

  if (!clean) return;

  const utterance = new SpeechSynthesisUtterance(clean);
  if (_selectedVoice) utterance.voice = _selectedVoice;
  utterance.rate = 1.0;
  utterance.pitch = 1.05;
  utterance.volume = 0.9;

  utterance.onstart = () => {
    _isSpeaking = true;
    if (_ttsBtn) _ttsBtn.classList.add('playing');
    if (window.avatarRenderer) window.avatarRenderer.setSpeaking(true);
  };

  utterance.onend = () => {
    _isSpeaking = false;
    if (_ttsBtn) _ttsBtn.classList.remove('playing');
    if (window.avatarRenderer) window.avatarRenderer.setSpeaking(false);
    if (_handsFreeMode) setTimeout(() => _startListening(), 400);
  };

  utterance.onerror = () => {
    _isSpeaking = false;
    if (_ttsBtn) _ttsBtn.classList.remove('playing');
    if (window.avatarRenderer) window.avatarRenderer.setSpeaking(false);
    if (_handsFreeMode) setTimeout(() => _startListening(), 400);
  };

  speechSynthesis.speak(utterance);
}

function _toggleTTS() {
  _ttsEnabled = !_ttsEnabled;

  if (_ttsEnabled) {
    if (_ttsBtn) _ttsBtn.classList.add('active');
    showToast('Voice output enabled', 'success', 2000);
  } else {
    if (_ttsBtn) _ttsBtn.classList.remove('active');
    showToast('Voice output disabled', 'info', 2000);
    if ('speechSynthesis' in window) speechSynthesis.cancel();
    if (_handsFreeMode) {
      _handsFreeMode = false;
      if (_handsFreeBtn) _handsFreeBtn.classList.remove('active');
    }
  }

  if (_ws && _ws.readyState === WebSocket.OPEN) {
    _ws.send(JSON.stringify({ type: 'tts_toggle', enabled: _ttsEnabled }));
  }
}

function _toggleHandsFree() {
  _handsFreeMode = !_handsFreeMode;

  if (_handsFreeMode) {
    if (!_ttsEnabled) _toggleTTS();
    if (_handsFreeBtn) _handsFreeBtn.classList.add('active');
    showToast('Hands-free mode ON — speak naturally', 'success', 2000);
    _startListening();
  } else {
    if (_handsFreeBtn) _handsFreeBtn.classList.remove('active');
    showToast('Hands-free mode OFF', 'info', 2000);
    _stopListening();
  }
}

// ---------------------------------------------------------------------------
// Message search
// ---------------------------------------------------------------------------

function _toggleMessageSearch() {
  const existing = document.getElementById('message-search');
  if (existing) { _closeMessageSearch(); return; }

  const searchBar = document.createElement('div');
  searchBar.id = 'message-search';
  searchBar.className = 'message-search';
  searchBar.innerHTML = `
    <input type="text" id="search-input" placeholder="Search messages..." autocomplete="off">
    <button id="search-close" class="search-close">&times;</button>
  `;

  const messagesEl = document.getElementById('messages');
  if (messagesEl && messagesEl.parentElement) {
    messagesEl.parentElement.insertBefore(searchBar, messagesEl);
  }

  const input = document.getElementById('search-input');
  const closeBtn = document.getElementById('search-close');
  if (input) input.focus();

  if (input) {
    input.addEventListener('input', () => {
      const query = input.value.toLowerCase();
      document.querySelectorAll('.message').forEach(m => {
        const text = m.textContent.toLowerCase();
        m.style.display = (query && !text.includes(query)) ? 'none' : '';
      });
    });
  }

  if (closeBtn) {
    closeBtn.addEventListener('click', () => _closeMessageSearch());
  }
}

function _closeMessageSearch() {
  const searchBar = document.getElementById('message-search');
  if (searchBar) {
    searchBar.remove();
    document.querySelectorAll('.message').forEach(m => { m.style.display = ''; });
  }
}

// ---------------------------------------------------------------------------
// Conversation export
// ---------------------------------------------------------------------------

function _exportConversation() {
  if (!_messages) return;
  const msgs = _messages.querySelectorAll('.message');
  if (msgs.length === 0) {
    showToast('No messages to export', 'warning');
    return;
  }

  let md = `# Cortana Conversation\n`;
  md += `**Exported:** ${new Date().toLocaleString()}\n`;
  md += `**System:** Sentient Core v2 / Jetson Orin Nano\n\n---\n\n`;

  msgs.forEach(msg => {
    const isUser = msg.classList.contains('user');
    const bubble = msg.querySelector('.message-bubble');
    if (!bubble) return;
    const text = bubble.textContent || bubble.innerText;
    const timeEl = msg.querySelector('.message-timestamp');
    const time = timeEl ? timeEl.textContent : '';
    const role = isUser ? 'You' : 'Cortana';
    md += `### ${role}${time ? ' (' + time + ')' : ''}\n${text}\n\n`;
  });

  md += `---\n*Exported from Sentient Core Neural Interface*\n`;

  const blob = new Blob([md], { type: 'text/markdown' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `cortana-chat-${new Date().toISOString().slice(0, 10)}.md`;
  a.click();
  URL.revokeObjectURL(url);
  showToast('Conversation exported', 'success', 2000);
}

// ---------------------------------------------------------------------------
// Keyboard shortcuts overlay
// ---------------------------------------------------------------------------

function _showShortcutsOverlay() {
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
    <h3 style="color:#00ffff; margin-bottom:16px; font-size:1rem; letter-spacing:2px;">KEYBOARD SHORTCUTS</h3>
    <div style="display:grid; grid-template-columns:auto 1fr; gap:8px 16px; font-size:0.85rem;">
      <kbd style="color:#00ffff">Enter</kbd><span>Send message</span>
      <kbd style="color:#00ffff">Shift+Enter</kbd><span>New line</span>
      <kbd style="color:#00ffff">Escape</kbd><span>Close search / Clear input</span>
      <kbd style="color:#00ffff">Ctrl+E</kbd><span>Export conversation</span>
      <kbd style="color:#00ffff">Ctrl+F</kbd><span>Search messages</span>
      <kbd style="color:#00ffff">?</kbd><span>Show this help</span>
    </div>
    <p style="margin-top:16px; color:#555; font-size:0.75rem; text-align:center;">Press Escape or click outside to close</p>
  `;

  overlay.appendChild(card);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
}

// ---------------------------------------------------------------------------
// PWA install banner
// ---------------------------------------------------------------------------

function _initInstallPrompt() {
  if (localStorage.getItem('cortana-install-dismissed')) return;

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    _deferredInstallPrompt = e;
    _showInstallBanner();
  });

  window.addEventListener('appinstalled', () => {
    _hideInstallBanner();
    showToast('Cortana installed successfully!', 'success');
    _deferredInstallPrompt = null;
  });
}

function _showInstallBanner() {
  if (document.getElementById('install-banner')) return;

  const banner = document.createElement('div');
  banner.id = 'install-banner';
  banner.className = 'install-banner';
  banner.innerHTML = `
    <div class="install-text">Install Cortana on your device</div>
    <button class="btn-install" id="install-btn">INSTALL</button>
    <button class="btn-dismiss" id="install-dismiss">&times;</button>
  `;

  const chatBody = document.getElementById('panel-chat-body');
  if (chatBody) {
    chatBody.insertBefore(banner, chatBody.firstChild);
  } else {
    document.body.insertBefore(banner, document.body.firstChild);
  }

  document.getElementById('install-btn')?.addEventListener('click', async () => {
    if (!_deferredInstallPrompt) return;
    _deferredInstallPrompt.prompt();
    await _deferredInstallPrompt.userChoice;
    _deferredInstallPrompt = null;
    _hideInstallBanner();
  });

  document.getElementById('install-dismiss')?.addEventListener('click', () => {
    localStorage.setItem('cortana-install-dismissed', 'true');
    _hideInstallBanner();
  });
}

function _hideInstallBanner() {
  document.getElementById('install-banner')?.remove();
}
