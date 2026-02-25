/**
 * SENTIENT CORE - AVATAR PANEL
 * ES module. Thin wrapper around avatar-hologram.js which has already
 * created window.avatarRenderer by the time this module runs.
 * Handles canvas sizing within the panel and routes system-status
 * updates (mood/emotion) to the renderer.
 */

let _observer = null;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export function init() {
  const canvas = document.getElementById('avatar-canvas');
  const body   = document.getElementById('panel-avatar-body');
  if (!canvas || !body) return;

  const resize = () => {
    const rect = body.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    canvas.width  = rect.width;
    canvas.height = rect.height;
    if (window.avatarRenderer?.setupCanvas) {
      window.avatarRenderer.setupCanvas();
    }
  };

  resize();

  _observer = new ResizeObserver(resize);
  _observer.observe(body);
}

export function update(data) {
  if (data.mood?.emotion && window.avatarRenderer?.setEmotion) {
    window.avatarRenderer.setEmotion(
      data.mood.emotion,
      data.mood.intensity ?? 0.5
    );
  }
}

export function destroy() {
  if (_observer) {
    _observer.disconnect();
    _observer = null;
  }
}
