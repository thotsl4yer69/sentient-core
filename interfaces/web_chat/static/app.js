import { GridManager } from './grid.js';
import { showToast, playSound, startTimestampUpdater } from './utils.js';
import * as chatPanel from './panels/chat.js';
import * as avatarPanel from './panels/avatar.js';
import * as vitalsPanel from './panels/vitals.js';
import * as moodPanel from './panels/mood.js';
import * as visionPanel from './panels/vision.js';
import * as networkPanel from './panels/network.js';
import * as remindersPanel from './panels/reminders.js';

// All panels
const panels = { chat: chatPanel, avatar: avatarPanel, vitals: vitalsPanel, mood: moodPanel, vision: visionPanel, network: networkPanel, reminders: remindersPanel };

let ws = null;
let reconnectTimer = null;
const RECONNECT_INTERVAL = 3000;
let grid = null;
let statusRefreshInterval = null;

// ==== INIT ====
function init() {
  // Initialize grid manager
  grid = new GridManager('#grid-container');
  grid.init();

  // Initialize all panels
  Object.values(panels).forEach(p => p.init());

  // Give chat panel the WS send capability
  chatPanel.setWebSocket(null); // Will be set on connect

  // Connect WebSocket
  connectWebSocket();

  // Start system clock
  startClock();

  // Start relative timestamp updater
  startTimestampUpdater();

  // Start periodic status refresh (for dashboard panels)
  startStatusRefresh();

  // Setup header controls
  setupHeaderControls();

  // Keyboard shortcuts
  setupKeyboardShortcuts();

  // Visibility change handler
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && (!ws || ws.readyState !== WebSocket.OPEN)) {
      connectWebSocket();
    }
  });
}

// ==== WEBSOCKET ====
function connectWebSocket() {
  updateConnectionStatus('connecting');
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${protocol}//${location.host}/ws`;

  try {
    ws = new WebSocket(url);

    ws.onopen = () => {
      updateConnectionStatus('connected');
      clearTimeout(reconnectTimer);
      playSound('connect');
      showToast('Neural link established', 'success', 2000);
      chatPanel.setWebSocket(ws);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleMessage(data);
      } catch (e) {
        console.error('WS parse error:', e);
      }
    };

    ws.onerror = () => updateConnectionStatus('error');
    ws.onclose = () => {
      updateConnectionStatus('disconnected');
      chatPanel.setWebSocket(null);
      reconnectTimer = setTimeout(connectWebSocket, RECONNECT_INTERVAL);
    };
  } catch (e) {
    updateConnectionStatus('error');
    reconnectTimer = setTimeout(connectWebSocket, RECONNECT_INTERVAL);
  }
}

function handleMessage(data) {
  switch (data.type) {
    case 'history':
      chatPanel.loadHistory(data.messages);
      break;
    case 'message':
      chatPanel.addMessage(data.message);
      break;
    case 'stream':
      chatPanel.handleStream(data);
      break;
    case 'thinking':
      chatPanel.showThinking(data.active, data.stage);
      break;
    case 'emotion':
      updateEmotion(data.emotion, data.intensity);
      break;
    case 'welcome':
      chatPanel.showWelcome(data);
      break;
    case 'system_status':
      updateAllPanels(data);
      break;
    case 'persona_state':
      if (data.state === 'deep_contemplating') chatPanel.showContemplation();
      break;
    case 'thought_stream':
      chatPanel.populateContemplation(data);
      break;
    case 'tts_audio':
      chatPanel.playTTSAudio?.(data.audio, data.format, data.phonemes, data.duration);
      break;
    case 'tts_phonemes':
      if (window.avatarRenderer?.processPhonemes) {
        window.avatarRenderer.processPhonemes({ phonemes: data.phonemes, duration: data.duration });
      }
      break;
    case 'speaking':
      if (window.avatarRenderer?.setSpeaking) {
        window.avatarRenderer.setSpeaking(data.active, data.text || '');
      }
      break;
    case 'mqtt_status':
      if (data.status === 'disconnected') showToast('MQTT link lost', 'warning');
      break;
    case 'error':
      showToast(data.message, 'error');
      break;
    case 'diagnostic_request':
      if (window.avatarRenderer?.getDiagnostics && ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'diagnostic_response', data: window.avatarRenderer.getDiagnostics() }));
      }
      break;
    case 'pong':
      break;
    default:
      console.log('Unknown WS type:', data.type);
  }
}

// ==== STATUS REFRESH ====
function startStatusRefresh() {
  fetchStatus();
  statusRefreshInterval = setInterval(fetchStatus, 15000);
}

async function fetchStatus() {
  try {
    const resp = await fetch('/api/status');
    if (resp.ok) {
      const data = await resp.json();
      updateAllPanels(data);
    }
  } catch (e) {
    console.error('Status fetch failed:', e);
  }
}

function updateAllPanels(data) {
  vitalsPanel.update(data);
  moodPanel.update(data);
  visionPanel.update(data);
  networkPanel.update(data);
  remindersPanel.update(data);
  avatarPanel.update(data);

  // Update header emotion from status
  if (data.mood?.emotion) {
    updateEmotion(data.mood.emotion, data.mood.intensity || 0.5);
  }
}

// ==== HEADER ====
function updateConnectionStatus(status) {
  const dot = document.getElementById('connection-dot');
  const text = document.getElementById('connection-text');
  if (dot) {
    dot.className = 'status-dot ' + (status === 'connected' ? 'connected' : status === 'connecting' ? 'connecting' : 'disconnected');
  }
  if (text) {
    text.textContent = status === 'connected' ? 'ONLINE' : status === 'connecting' ? 'CONNECTING' : 'OFFLINE';
  }
  // Dim avatar when disconnected
  const canvas = document.getElementById('avatar-canvas');
  if (canvas) {
    canvas.style.opacity = status === 'connected' ? '1' : '0.3';
    canvas.style.transition = 'opacity 0.5s ease';
  }
}

function updateEmotion(emotion, intensity = 0.5) {
  const text = document.getElementById('emotion-text');
  if (text) text.textContent = emotion.toUpperCase();

  // Update avatar
  if (window.avatarRenderer?.setEmotion) {
    window.avatarRenderer.setEmotion(emotion, intensity);
  }
  // Update neural background
  if (window.setNeuralMood) {
    window.setNeuralMood(emotion);
  }
}

function startClock() {
  const el = document.getElementById('system-time');
  if (!el) return;
  const tick = () => {
    const now = new Date();
    el.textContent = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;
  };
  tick();
  setInterval(tick, 30000); // Update every 30s (just HH:MM)
}

function setupHeaderControls() {
  // Reset layout button
  document.getElementById('reset-layout-btn')?.addEventListener('click', () => grid?.resetLayout());

  // Preset switcher dropdown toggle
  const presetBtn = document.getElementById('preset-switcher-btn');
  const presetDropdown = document.getElementById('preset-dropdown');
  if (presetBtn && presetDropdown) {
    presetBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      presetDropdown.classList.toggle('open');
    });
    document.addEventListener('click', () => presetDropdown.classList.remove('open'));
  }

  // Preset buttons
  document.querySelectorAll('[data-preset]').forEach(btn => {
    btn.addEventListener('click', () => {
      grid?.setPreset(btn.dataset.preset);
      // Update active state
      document.querySelectorAll('[data-preset]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      presetDropdown?.classList.remove('open');
    });
  });
}

function setupKeyboardShortcuts() {
  // Only grid-level shortcuts here — Ctrl+E, Ctrl+F, ? are handled by chat.js
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && grid?.maximizedPanel) {
      grid.maximizePanel(grid.maximizedPanel);
    }
  });
}

// Keep-alive ping
setInterval(() => {
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'ping' }));
  }
}, 30000);

// ==== BOOT ====
document.addEventListener('DOMContentLoaded', init);

// Capture JS errors for diagnostics
window._jsErrors = [];
window.addEventListener('error', (e) => {
  window._jsErrors.push({ message: e.message, filename: e.filename, lineno: e.lineno, time: Date.now() });
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'diagnostic', data: { source: 'js_error', message: e.message, filename: e.filename, lineno: e.lineno } }));
  }
});
