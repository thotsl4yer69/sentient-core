import { escapeHtml } from '../utils.js';

let visionWs = null;
let reconnectTimer = null;
let snapshotTimer = null;
let feedActive = false;
let activeNode = 'jetson';
let detectionCount = 0;
let nodeFps = {};

export function init() {
  connectVisionWs();
  startSnapshotPolling();

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

// ── Snapshot Polling (more reliable than MJPEG proxy) ──
function startSnapshotPolling() {
  const img = document.getElementById('vision-feed');
  const overlay = document.getElementById('vision-feed-overlay');
  if (!img) return;

  let firstLoad = true;

  // Show connecting state immediately
  if (overlay) {
    overlay.style.display = 'flex';
    overlay.innerHTML = '<span class="vision-feed-placeholder">CONNECTING...</span>';
  }

  function loadSnapshot() {
    const ts = Date.now();
    const testImg = new Image();
    testImg.onload = () => {
      img.src = testImg.src;
      img.style.display = 'block';
      if (overlay) overlay.style.display = 'none';
      feedActive = true;
      firstLoad = false;
    };
    testImg.onerror = () => {
      if (overlay) {
        overlay.style.display = 'flex';
        overlay.innerHTML = firstLoad
          ? '<span class="vision-feed-placeholder">CONNECTING...</span>'
          : '<span class="vision-feed-placeholder">FEED OFFLINE</span>';
      }
      feedActive = false;
      firstLoad = false;
    };
    testImg.src = `/api/vision/snapshot/${activeNode}?t=${ts}`;
  }

  loadSnapshot();
  snapshotTimer = setInterval(loadSnapshot, 500);
}

function switchFeed(node) {
  activeNode = node;
  clearInterval(snapshotTimer);

  // Clear detection list immediately on switch
  const listEl = document.getElementById('vision-detection-list');
  if (listEl) listEl.innerHTML = '<span class="text-muted">SWITCHING...</span>';
  detectionCount = 0;
  const badge = document.getElementById('vision-det-badge');
  if (badge) badge.style.display = 'none';

  startSnapshotPolling();

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
export function update(data) {
  const security = data.security || {};
  const level = security.threat_level || 0;
  const threats = security.active_threats || [];
  const jack = security.jack_present;
  const ambient = security.ambient_state || 'unknown';
  const nodes = security.nodes || {};

  // Threat level
  let color, label;
  if (level <= 3) { color = '#22c55e'; label = 'ALL CLEAR'; }
  else if (level <= 6) { color = '#f59e0b'; label = 'ELEVATED'; }
  else { color = '#ef4444'; label = 'CRITICAL'; }

  const levelEl = document.getElementById('threat-level');
  const labelEl = document.getElementById('threat-label');
  const meterFill = document.getElementById('threat-meter-fill');

  if (levelEl) { levelEl.textContent = level; levelEl.style.color = color; }
  if (labelEl) { labelEl.textContent = label; labelEl.style.color = color; }
  if (meterFill) { meterFill.style.width = (level / 10 * 100) + '%'; meterFill.style.background = color; }

  // Jack + ambient
  const jackEl = document.getElementById('jack-status');
  const ambientEl = document.getElementById('ambient-status');
  if (jackEl) {
    const dotClass = jack ? 'connected' : (jack === false ? 'disconnected' : 'dim');
    const text = jack ? 'JACK HOME' : (jack === false ? 'JACK AWAY' : 'JACK --');
    jackEl.innerHTML = `<span class="status-dot ${dotClass}"></span>${text}`;
  }
  if (ambientEl) {
    ambientEl.innerHTML = `<span class="status-dot connected"></span>${escapeHtml(ambient.toUpperCase())}`;
  }

  // Active threats
  const threatsEl = document.getElementById('vision-threats');
  if (threatsEl) {
    if (threats.length === 0) {
      threatsEl.style.display = 'none';
    } else {
      threatsEl.style.display = 'block';
      threatsEl.innerHTML = threats.slice(0, 5).map(t =>
        `<div class="threat-row"><span class="threat-sev">${t.severity || '?'}</span><span>${escapeHtml(t.type || 'unknown')} — ${escapeHtml(t.source || '')}</span></div>`
      ).join('');
    }
  }

  // Vision nodes — clickable to switch feeds
  const nodesEl = document.getElementById('vision-nodes');
  if (nodesEl) {
    const nodeNames = Object.keys(nodes).filter(n => n !== 'network');
    if (nodeNames.length === 0) {
      nodesEl.innerHTML = '<span class="text-muted">NO VISION NODES</span>';
    } else {
      nodesEl.innerHTML = nodeNames.map(name => {
        const info = nodes[name];
        const online = info.online;
        const dets = Array.isArray(info.detections) ? info.detections.length : 0;
        const isActive = name === activeNode;
        const fps = nodeFps[name] || (info.fps || 0);

        // Last-seen age for offline nodes
        const lastSeen = info.last_seen;
        let ageText = '';
        if (lastSeen && !online) {
          const ago = Math.round((Date.now() - new Date(lastSeen).getTime()) / 1000);
          if (ago < 60) ageText = `${ago}s ago`;
          else if (ago < 3600) ageText = `${Math.round(ago / 60)}m ago`;
          else ageText = `${Math.round(ago / 3600)}h ago`;
        }

        let metaParts = [];
        if (online) {
          metaParts.push(`${dets} obj`);
          if (fps) metaParts.push(`<span class="node-fps">${fps} FPS</span>`);
        } else if (ageText) {
          metaParts.push(`<span class="node-age">${ageText}</span>`);
        }
        const metaHtml = metaParts.length ? `<div class="node-meta">${metaParts.join(' • ')}</div>` : '';

        return `<div class="node-row ${isActive ? 'active-node' : ''}" data-node="${escapeHtml(name)}" style="cursor:pointer">
          <span class="status-dot ${online ? 'connected' : 'disconnected'}"></span>
          <div class="node-info"><div class="node-name">${escapeHtml(name.toUpperCase())}</div>${metaHtml}</div>
          <span class="node-badge ${online ? 'online' : 'offline'}">${online ? 'ONLINE' : 'OFFLINE'}</span>
        </div>`;
      }).join('');
    }
  }
}

export function destroy() {
  if (visionWs) { visionWs.close(); visionWs = null; }
  clearTimeout(reconnectTimer);
  clearInterval(snapshotTimer);
}
