import { escapeHtml } from '../utils.js';

function barColor(pct) {
  if (pct <= 60) return '#22c55e';  // green
  if (pct <= 80) return '#f59e0b';  // warning
  return '#ef4444';                  // danger
}

export function init() {
  // Nothing to init - DOM is static
}

export function update(data) {
  const { stats, services } = data;

  // GPU bar
  if (stats?.gpu) {
    const m = stats.gpu.match(/(\d+)%/);
    const pct = m ? parseInt(m[1]) : 0;
    const fill = document.getElementById('vitals-gpu-fill');
    const val = document.getElementById('vitals-gpu-val');
    if (fill) { fill.style.width = pct + '%'; fill.style.background = barColor(pct); fill.style.boxShadow = '0 0 6px ' + barColor(pct); }
    if (val) { val.textContent = stats.gpu; val.style.color = barColor(pct); }
  }

  // RAM bar - parse "X.XG/Y.YG" format
  if (stats?.ram) {
    const m = stats.ram.match(/([\d.]+)G?\/([\d.]+)G?/);
    const pct = m ? Math.round((parseFloat(m[1]) / parseFloat(m[2])) * 100) : 0;
    const fill = document.getElementById('vitals-ram-fill');
    const val = document.getElementById('vitals-ram-val');
    if (fill) { fill.style.width = pct + '%'; fill.style.background = barColor(pct); fill.style.boxShadow = '0 0 6px ' + barColor(pct); }
    if (val) { val.textContent = stats.ram; val.style.color = barColor(pct); }
  }

  // Disk bar
  if (stats?.disk) {
    const m = stats.disk.match(/(\d+)%/);
    const pct = m ? parseInt(m[1]) : 0;
    const fill = document.getElementById('vitals-disk-fill');
    const val = document.getElementById('vitals-disk-val');
    if (fill) { fill.style.width = pct + '%'; fill.style.background = barColor(pct); fill.style.boxShadow = '0 0 6px ' + barColor(pct); }
    if (val) { val.textContent = stats.disk; val.style.color = barColor(pct); }
  }

  // Uptime
  const uptimeEl = document.getElementById('vitals-uptime');
  if (uptimeEl && stats?.uptime) {
    uptimeEl.textContent = 'UPTIME ' + stats.uptime;
  }

  // Services grid
  const grid = document.getElementById('service-grid');
  if (grid && services) {
    grid.innerHTML = '';
    for (const [name, status] of Object.entries(services)) {
      const short = name.replace('sentient-', '');
      const active = status === 'active';
      const el = document.createElement('div');
      el.className = 'service-item';
      el.innerHTML = `<span class="service-dot" style="background:${active ? 'var(--accent-success)' : 'var(--accent-danger)'}"></span><span>${escapeHtml(short)}</span>`;
      grid.appendChild(el);
    }
  }
}

export function destroy() {}
