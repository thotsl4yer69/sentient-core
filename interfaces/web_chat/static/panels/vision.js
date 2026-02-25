import { escapeHtml } from '../utils.js';

let visionWs = null;
let reconnectTimer = null;
let currentDetections = [];
let feedActive = false;

export function init() {
  connectVisionWs();
  startFeed();
}

function connectVisionWs() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${protocol}//${location.host}/ws/vision`;

  try {
    visionWs = new WebSocket(url);

    visionWs.onopen = () => {
      clearTimeout(reconnectTimer);
    };

    visionWs.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'detection') {
          handleDetection(data);
        }
      } catch (e) {
        console.error('Vision WS parse error:', e);
      }
    };

    visionWs.onclose = () => {
      reconnectTimer = setTimeout(connectVisionWs, 5000);
    };

    visionWs.onerror = () => {
      visionWs.close();
    };
  } catch (e) {
    reconnectTimer = setTimeout(connectVisionWs, 5000);
  }
}

function startFeed() {
  const img = document.getElementById('vision-feed');
  const overlay = document.getElementById('vision-feed-overlay');
  if (!img) return;

  // Try Jetson MJPEG stream via proxy
  img.src = '/api/vision/stream/jetson';
  img.onload = () => {
    img.style.display = 'block';
    if (overlay) overlay.style.display = 'none';
    feedActive = true;
  };
  img.onerror = () => {
    img.style.display = 'none';
    if (overlay) {
      overlay.style.display = 'flex';
      overlay.innerHTML = '<span class="vision-feed-placeholder">FEED OFFLINE</span>';
    }
    feedActive = false;
    // Retry in 10s
    setTimeout(startFeed, 10000);
  };
}

function handleDetection(data) {
  const objects = data.objects || [];
  const source = data.source || 'unknown';
  currentDetections = objects;

  const listEl = document.getElementById('vision-detection-list');
  if (!listEl) return;

  if (objects.length === 0) {
    listEl.innerHTML = '<span class="text-muted">CLEAR</span>';
    return;
  }

  listEl.innerHTML = objects.slice(0, 8).map(obj => {
    const conf = Math.round((obj.confidence || 0) * 100);
    const cls = escapeHtml(obj.class || 'unknown');
    const barColor = conf > 70 ? '#22c55e' : conf > 40 ? '#f59e0b' : '#ef4444';
    return `<div class="detection-row">
      <span class="detection-class">${cls.toUpperCase()}</span>
      <div class="detection-bar"><div class="detection-bar-fill" style="width:${conf}%;background:${barColor}"></div></div>
      <span class="detection-conf">${conf}%</span>
    </div>`;
  }).join('');
}

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

  // Vision nodes
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
        const meta = online ? `${dets} object${dets !== 1 ? 's' : ''}` : '';
        return `<div class="node-row">
          <span class="status-dot ${online ? 'connected' : 'disconnected'}"></span>
          <div class="node-info"><div class="node-name">${escapeHtml(name.toUpperCase())}</div>${meta ? `<div class="node-meta">${escapeHtml(meta)}</div>` : ''}</div>
          <span class="node-badge ${online ? 'online' : 'offline'}">${online ? 'ONLINE' : 'OFFLINE'}</span>
        </div>`;
      }).join('');
    }
  }
}

export function destroy() {
  if (visionWs) {
    visionWs.close();
    visionWs = null;
  }
  clearTimeout(reconnectTimer);
}
