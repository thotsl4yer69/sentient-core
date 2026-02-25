import { escapeHtml } from '../utils.js';

export function init() {}

export function update(data) {
  const devices = data.network_devices || [];
  const security = data.security || {};
  const netInfo = security.nodes?.network || {};

  const total = netInfo.device_count || devices.length;
  const known = netInfo.known_count || devices.filter(d => d.known).length;

  const countEl = document.getElementById('network-count');
  if (countEl) countEl.textContent = `${total} DEVICES (${known} KNOWN)`;

  const listEl = document.getElementById('network-list');
  if (!listEl || devices.length === 0) return;

  const sorted = [...devices].sort((a, b) => {
    if (a.known !== b.known) return a.known ? -1 : 1;
    return (a.name || a.hostname || a.ip || '').localeCompare(b.name || b.hostname || b.ip || '');
  });

  listEl.innerHTML = sorted.slice(0, 30).map(d => {
    const name = d.name || d.hostname || 'Unknown';
    const dotColor = d.known ? 'var(--accent-success)' : 'var(--accent-warning)';
    return `<div class="device-row">
      <span class="status-dot" style="background:${dotColor}"></span>
      <span class="device-name">${escapeHtml(name)}</span>
      <span class="device-ip">${escapeHtml(d.ip || '')}</span>
    </div>`;
  }).join('');
}

export function destroy() {}
