// grid.js - Panel Grid Manager
const STORAGE_KEY = 'cortana-grid-layout';

const PRESETS = {
  'default':      { cols: '1.2fr 1fr 0.8fr', rows: '1fr 1fr 1fr' },
  'focus-chat':   { cols: '2fr 0.8fr 0.6fr', rows: '1fr 1fr 0.8fr' },
  'monitor':      { cols: '0.6fr 0.6fr 1.5fr', rows: '1fr 1fr 1fr' }
};

const DEFAULT_PRESET = 'default';

const PANEL_IDS = ['panel-chat', 'panel-avatar', 'panel-vitals', 'panel-mood', 'panel-vision', 'panel-network', 'panel-reminders'];

export class GridManager {
  constructor(containerSelector = '#grid-container') {
    this.container = document.querySelector(containerSelector);
    this.panels = {};
    this.collapsed = new Set();
    this.maximizedPanel = null;
    this._resizeState = null;
  }

  init() {
    if (!this.container) return;

    // Discover panels
    PANEL_IDS.forEach(id => {
      const el = document.getElementById(id);
      if (el) this.panels[id] = el;
    });

    // Apply default layout first, then try to restore saved
    this.setPreset(DEFAULT_PRESET);
    this.loadLayout();

    // Add resize handles
    this._createResizeHandles();

    // Bind panel header buttons
    this._bindPanelButtons();
  }

  _createResizeHandles() {
    // Remove existing handles
    this.container.querySelectorAll('.grid-resize-handle-v, .grid-resize-handle-h').forEach(el => el.remove());

    // Vertical handles between columns (col index 1 and 2 in a 3-col grid)
    for (let i = 1; i <= 2; i++) {
      const handle = document.createElement('div');
      handle.className = 'grid-resize-handle-v';
      handle.dataset.index = i;
      handle.style.gridColumn = `${i} / ${i + 1}`;
      handle.style.gridRow = '1 / -1';
      handle.style.cursor = 'col-resize';
      handle.style.zIndex = '10';
      this._bindResizeHandle(handle, 'col', i - 1);
      this.container.appendChild(handle);
    }

    // Horizontal handles between rows (row index 1 and 2 in a 3-row grid)
    for (let i = 1; i <= 2; i++) {
      const handle = document.createElement('div');
      handle.className = 'grid-resize-handle-h';
      handle.dataset.index = i;
      handle.style.gridColumn = '1 / -1';
      handle.style.gridRow = `${i} / ${i + 1}`;
      handle.style.cursor = 'row-resize';
      handle.style.zIndex = '10';
      this._bindResizeHandle(handle, 'row', i - 1);
      this.container.appendChild(handle);
    }
  }

  _bindResizeHandle(handle, direction, index) {
    const onStart = (e) => {
      e.preventDefault();
      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const clientY = e.touches ? e.touches[0].clientY : e.clientY;

      const colsStr = getComputedStyle(this.container).gridTemplateColumns;
      const rowsStr = getComputedStyle(this.container).gridTemplateRows;

      this._resizeState = {
        direction,
        index,
        startX: clientX,
        startY: clientY,
        startCols: this._parsePx(colsStr),
        startRows: this._parsePx(rowsStr),
        containerW: this.container.offsetWidth,
        containerH: this.container.offsetHeight
      };

      document.addEventListener('mousemove', onMove);
      document.addEventListener('touchmove', onMove, { passive: false });
      document.addEventListener('mouseup', onEnd);
      document.addEventListener('touchend', onEnd);
      document.body.style.cursor = direction === 'col' ? 'col-resize' : 'row-resize';
      document.body.style.userSelect = 'none';
    };

    const onMove = (e) => {
      if (!this._resizeState) return;
      e.preventDefault();
      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const clientY = e.touches ? e.touches[0].clientY : e.clientY;
      const s = this._resizeState;

      if (s.direction === 'col') {
        const delta = clientX - s.startX;
        const sizes = [...s.startCols];
        // Redistribute delta between index and index+1
        const minSize = 40;
        const newLeft = Math.max(minSize, sizes[s.index] + delta);
        const newRight = Math.max(minSize, sizes[s.index + 1] - delta);
        const actual = sizes[s.index] + sizes[s.index + 1];
        if (newLeft + newRight <= actual) {
          sizes[s.index] = newLeft;
          sizes[s.index + 1] = actual - newLeft;
        }
        const totalW = sizes.reduce((a, b) => a + b, 0);
        const frSizes = sizes.map(px => `${(px / totalW * 3).toFixed(4)}fr`);
        this.container.style.gridTemplateColumns = frSizes.join(' ');
      } else {
        const delta = clientY - s.startY;
        const sizes = [...s.startRows];
        const minSize = 40;
        const newTop = Math.max(minSize, sizes[s.index] + delta);
        const actual = sizes[s.index] + sizes[s.index + 1];
        if (newTop <= actual - minSize) {
          sizes[s.index] = newTop;
          sizes[s.index + 1] = actual - newTop;
        }
        const totalH = sizes.reduce((a, b) => a + b, 0);
        const frSizes = sizes.map(px => `${(px / totalH * 3).toFixed(4)}fr`);
        this.container.style.gridTemplateRows = frSizes.join(' ');
      }
    };

    const onEnd = () => {
      this._resizeState = null;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('touchmove', onMove);
      document.removeEventListener('mouseup', onEnd);
      document.removeEventListener('touchend', onEnd);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      this.saveLayout();
    };

    handle.addEventListener('mousedown', onStart);
    handle.addEventListener('touchstart', onStart, { passive: false });
  }

  // Parse computed grid-template-columns/rows (pixel values) into array of numbers
  _parsePx(str) {
    return str.trim().split(/\s+/).map(v => parseFloat(v));
  }

  _bindPanelButtons() {
    this.container.querySelectorAll('.panel-collapse').forEach(btn => {
      const panel = btn.closest('.panel');
      if (!panel) return;
      btn.addEventListener('click', () => this.collapsePanel(panel.id));
    });

    this.container.querySelectorAll('.panel-maximize').forEach(btn => {
      const panel = btn.closest('.panel');
      if (!panel) return;
      btn.addEventListener('click', () => this.maximizePanel(panel.id));
    });
  }

  collapsePanel(panelId) {
    const panel = document.getElementById(panelId);
    if (!panel) return;
    panel.classList.toggle('collapsed');
    if (panel.classList.contains('collapsed')) {
      this.collapsed.add(panelId);
    } else {
      this.collapsed.delete(panelId);
    }
    this.saveLayout();
  }

  maximizePanel(panelId) {
    const panel = document.getElementById(panelId);
    if (!panel) return;
    if (this.maximizedPanel === panelId) {
      panel.classList.remove('maximized');
      this.maximizedPanel = null;
    } else {
      if (this.maximizedPanel) {
        document.getElementById(this.maximizedPanel)?.classList.remove('maximized');
      }
      panel.classList.add('maximized');
      this.maximizedPanel = panelId;
    }
  }

  setPreset(name) {
    const preset = PRESETS[name];
    if (!preset || !this.container) return;
    this.container.style.gridTemplateColumns = preset.cols;
    this.container.style.gridTemplateRows = preset.rows;
    this.saveLayout();
  }

  saveLayout() {
    if (!this.container) return;
    const layout = {
      cols: this.container.style.gridTemplateColumns,
      rows: this.container.style.gridTemplateRows,
      collapsed: [...this.collapsed]
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  }

  loadLayout() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
      if (saved) {
        if (saved.cols) this.container.style.gridTemplateColumns = saved.cols;
        if (saved.rows) this.container.style.gridTemplateRows = saved.rows;
        if (Array.isArray(saved.collapsed)) {
          saved.collapsed.forEach(id => {
            const panel = document.getElementById(id);
            if (panel) {
              panel.classList.add('collapsed');
              this.collapsed.add(id);
            }
          });
        }
        return true;
      }
    } catch (e) { /* ignore corrupt storage */ }
    return false;
  }

  resetLayout() {
    localStorage.removeItem(STORAGE_KEY);
    this.collapsed.clear();
    if (this.maximizedPanel) {
      document.getElementById(this.maximizedPanel)?.classList.remove('maximized');
      this.maximizedPanel = null;
    }
    document.querySelectorAll('.panel.collapsed').forEach(p => p.classList.remove('collapsed'));
    // Apply default without saving (saveLayout will record defaults)
    const preset = PRESETS[DEFAULT_PRESET];
    if (preset && this.container) {
      this.container.style.gridTemplateColumns = preset.cols;
      this.container.style.gridTemplateRows = preset.rows;
    }
    this.saveLayout();
  }
}
