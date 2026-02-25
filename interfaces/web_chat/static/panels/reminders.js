import { escapeHtml } from '../utils.js';

export function init() {}

export function update(data) {
  const reminders = data.reminders || [];
  const listEl = document.getElementById('reminders-list');
  if (!listEl) return;

  if (reminders.length === 0) {
    listEl.innerHTML = '<span class="text-muted">NO ACTIVE REMINDERS</span>';
    return;
  }

  listEl.innerHTML = reminders.map(r => {
    const remaining = r.remaining || 0;
    let timeStr;
    if (remaining < 60) timeStr = 'NOW';
    else if (remaining < 3600) timeStr = Math.round(remaining / 60) + 'm';
    else if (remaining < 86400) timeStr = Math.round(remaining / 3600) + 'h';
    else timeStr = Math.round(remaining / 86400) + 'd';

    const urgent = remaining < 900;
    return `<div class="reminder-row${urgent ? ' urgent' : ''}">
      <span class="reminder-text">${escapeHtml(r.text || 'Reminder')}</span>
      <span class="reminder-time">${timeStr}</span>
    </div>`;
  }).join('');
}

export function destroy() {}
