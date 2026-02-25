// utils.js - Shared utility functions

// ---------------------------------------------------------------------------
// Markdown rendering (relies on globals: marked, DOMPurify, hljs)
// ---------------------------------------------------------------------------

export function renderMarkdown(text) {
  if (!text) return '';
  try {
    const rawHtml = marked.parse(text);
    return DOMPurify.sanitize(rawHtml, {
      ADD_TAGS: ['code', 'pre', 'span'],
      ADD_ATTR: ['class']
    });
  } catch (e) {
    return escapeHtml(text);
  }
}

// ---------------------------------------------------------------------------
// HTML escaping
// ---------------------------------------------------------------------------

export function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// ---------------------------------------------------------------------------
// Relative time formatting
// ---------------------------------------------------------------------------

export function getRelativeTime(date) {
  const now = new Date();
  const diffMs = now - date;
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHr  = Math.floor(diffMin / 60);
  if (diffSec < 10)  return 'just now';
  if (diffSec < 60)  return `${diffSec}s ago`;
  if (diffMin < 60)  return `${diffMin}m ago`;
  if (diffHr  < 24)  return `${diffHr}h ago`;
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// ---------------------------------------------------------------------------
// Notification sounds (Web Audio API)
// ---------------------------------------------------------------------------

let audioCtx = null;

function getAudioCtx() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  return audioCtx;
}

export function playSound(type = 'message') {
  try {
    const ctx = getAudioCtx();
    const osc  = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = 'sine';

    switch (type) {
      case 'message':
        osc.frequency.setValueAtTime(587, ctx.currentTime);
        osc.frequency.setValueAtTime(880, ctx.currentTime + 0.08);
        gain.gain.setValueAtTime(0.06, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.25);
        break;

      case 'send':
        osc.frequency.setValueAtTime(440, ctx.currentTime);
        osc.frequency.exponentialRampToValueAtTime(660, ctx.currentTime + 0.06);
        gain.gain.setValueAtTime(0.04, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.1);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.1);
        break;

      case 'proactive':
        osc.frequency.setValueAtTime(784, ctx.currentTime);
        osc.frequency.setValueAtTime(659, ctx.currentTime + 0.12);
        osc.frequency.setValueAtTime(523, ctx.currentTime + 0.24);
        gain.gain.setValueAtTime(0.05, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.4);
        break;

      case 'connect':
        osc.frequency.setValueAtTime(523, ctx.currentTime);
        osc.frequency.setValueAtTime(659, ctx.currentTime + 0.1);
        osc.frequency.setValueAtTime(784, ctx.currentTime + 0.2);
        gain.gain.setValueAtTime(0.05, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.4);
        break;

      default:
        osc.stop(ctx.currentTime);
        break;
    }
  } catch (e) { /* Audio not available */ }
}

// ---------------------------------------------------------------------------
// Toast notification system
// ---------------------------------------------------------------------------

const TOAST_ICONS = {
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
  success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
  warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  error:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>'
};

export function showToast(message, type = 'info', duration = 4000) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const icon  = TOAST_ICONS[type] || TOAST_ICONS.info;
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <div class="toast-icon">${icon}</div>
    <div class="toast-message">${escapeHtml(message)}</div>
    <button class="toast-dismiss">&times;</button>
  `;

  const dismiss = () => {
    toast.classList.add('toast-exit');
    setTimeout(() => toast.remove(), 300);
  };

  toast.querySelector('.toast-dismiss').addEventListener('click', dismiss);
  container.appendChild(toast);

  setTimeout(() => {
    if (toast.parentNode) dismiss();
  }, duration);
}

// ---------------------------------------------------------------------------
// Timestamp tracker — keeps relative-time labels fresh
// ---------------------------------------------------------------------------

const timestampElements = new Map();

export function trackTimestamp(element, date) {
  timestampElements.set(element, date);
}

export function startTimestampUpdater() {
  setInterval(() => {
    timestampElements.forEach((date, el) => {
      if (el.isConnected) {
        el.textContent = getRelativeTime(date);
      } else {
        timestampElements.delete(el);
      }
    });
  }, 30000);
}

// ---------------------------------------------------------------------------
// Configure marked at module load time
// ---------------------------------------------------------------------------

if (typeof marked !== 'undefined') {
  marked.setOptions({
    highlight: function(code, lang) {
      if (lang && typeof hljs !== 'undefined' && hljs.getLanguage(lang)) {
        return hljs.highlight(code, { language: lang }).value;
      }
      if (typeof hljs !== 'undefined') return hljs.highlightAuto(code).value;
      return code;
    },
    breaks: true,
    gfm: true
  });
}
