import { escapeHtml } from '../utils.js';

let visionWs = null;
let reconnectTimer = null;
let streamActive = false;
let feedFirstLoad = true;
let activeNode = 'jetson';
let detectionCount = 0;
let nodeFps = {};

export function init() {
  connectVisionWs();
  startMjpegStream();

  // Node switcher clicks
  document.getElementById('vision-nodes')?.addEventListener('click', (e) => {
    const row = e.target.closest('.node-row');
    if (row) {
      const name = row.dataset.node;
      if (name) switchFeed(name);
    }
  });
}

// ── Vision WebSocket ──
function connectVisionWs() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${protocol}//${location.host}/ws/vision`;

  try {
    visionWs = new WebSocket(url);
    visionWs.onopen = () => clearTimeout(reconnectTimer);
    visionWs.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'detection' && data.source === activeNode) {
          handleDetection(data);
        }
      } catch (e) { /* ignore */ }
    };
    visionWs.onclose = () => { reconnectTimer = setTimeout(connectVisionWs, 5000); };
    visionWs.onerror = () => visionWs.close();
  } catch (e) {
    reconnectTimer = setTimeout(connectVisionWs, 5000);
  }
}

// ── MJPEG Stream ──
function startMjpegStream() {
  const img = document.getElementById('vision-feed');
  const overlay = document.getElementById('vision-feed-overlay');
  if (!img) return;

  // Show connecting state
  if (overlay) {
    overlay.style.display = 'flex';
    overlay.innerHTML = '<span class="vision-feed-placeholder">CONNECTING...</span>';
  }
  img.style.display = 'none';
  feedFirstLoad = true;

  // Set MJPEG stream URL — browser handles continuous loading
  const streamUrl = `/api/vision/stream/${activeNode}`;

  img.onload = () => {
    if (feedFirstLoad) {
      // First frame received — stream is live
      img.style.display = 'block';
      if (overlay) overlay.style.display = 'none';
      feedFirstLoad = false;
      streamActive = true;
    }
  };

  img.onerror = () => {
    streamActive = false;
    if (overlay) {
      overlay.style.display = 'flex';
      overlay.innerHTML = '<span class="vision-feed-placeholder">FEED OFFLINE</span>';
    }
    img.style.display = 'none';
    // Retry after 3 seconds
    setTimeout(() => {
      if (!streamActive) {
        feedFirstLoad = true;
        if (overlay) overlay.innerHTML = '<span class="vision-feed-placeholder">RECONNECTING...</span>';
        img.src = `/api/vision/stream/${activeNode}?t=${Date.now()}`;
      }
    }, 3000);
  };

  img.src = streamUrl;
}

function switchFeed(node) {
  activeNode = node;

  // Clear detection list immediately on switch
  const listEl = document.getElementById('vision-detection-list');
  if (listEl) listEl.innerHTML = '<span class="text-muted">SWITCHING...</span>';
  detectionCount = 0;
  const badge = document.getElementById('vision-det-badge');
  if (badge) badge.style.display = 'none';

  startMjpegStream();

  // Update active highlight
  document.querySelectorAll('#vision-nodes .node-row').forEach(r => {
    r.classList.toggle('active-node', r.dataset.node === node);
  });
}

// ── Detection display ──
function handleDetection(data) {
  const objects = data.objects || [];
  detectionCount = objects.length;
  nodeFps[data.source] = data.fps || 0;

  // Update badge
  const badge = document.getElementById('vision-det-badge');
  if (badge) {
    badge.textContent = detectionCount;
    badge.style.display = detectionCount > 0 ? 'inline-block' : 'none';
  }

  const listEl = document.getElementById('vision-detection-list');
  if (!listEl) return;

  if (objects.length === 0) {
    listEl.innerHTML = '<span class="text-muted">CLEAR</span>';
    return;
  }

  const total = objects.length;
  const shown = Math.min(total, 8);
  listEl.innerHTML = objects.slice(0, shown).map(obj => {
    const conf = Math.round((obj.confidence || 0) * 100);
    const cls = escapeHtml(obj.class || 'unknown');
    const barColor = conf > 70 ? '#22c55e' : conf > 40 ? '#f59e0b' : '#ef4444';
    return `<div class="detection-row">
      <span class="detection-class">${cls.toUpperCase()}</span>
      <div class="detection-bar"><div class="detection-bar-fill" style="width:${conf}%;background:${barColor}"></div></div>
      <span class="detection-conf">${conf}%</span>
    </div>`;
  }).join('');
  if (total > shown) {
    listEl.innerHTML += `<div class="detection-overflow">+${total - shown} more</div>`;
  }
}

// ── Status update (from /api/status polling) ──
// v3.1: Simplified — threat bar, jack status, node list moved to compact status bar in app.js
export function update(data) {
  const security = data.security || {};
  const level = security.threat_level || 0;

  // Still update hidden elements so other code referencing them doesn't break
  const levelEl = document.getElementById('threat-level');
  if (levelEl) levelEl.textContent = level;

  // Update detection badge from status data (supplements WebSocket detections)
  const nodes = security.nodes || {};
  const nodeNames = Object.keys(nodes).filter(n => n !== 'network');
  const currentNode = nodeNames.find(n => n === activeNode) || nodeNames[0];
  if (currentNode && nodes[currentNode]?.online) {
    const info = nodes[currentNode];
    const dets = Array.isArray(info.detections) ? info.detections.length : 0;
    if (dets > 0 && detectionCount === 0) {
      // Update badge if we have status data but no WebSocket detections yet
      const badge = document.getElementById('vision-det-badge');
      if (badge) {
        badge.textContent = dets;
        badge.style.display = 'inline-block';
      }
    }
  }
}

export function destroy() {
  if (visionWs) { visionWs.close(); visionWs = null; }
  clearTimeout(reconnectTimer);
  const img = document.getElementById('vision-feed');
  if (img) img.src = '';
  streamActive = false;
}
