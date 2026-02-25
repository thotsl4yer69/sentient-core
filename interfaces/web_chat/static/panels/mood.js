import { escapeHtml } from '../utils.js';

const EMOJI_MAP = {
  joy: '😊', happiness: '😊', sadness: '😔', anger: '😠', curiosity: '🤔',
  playful: '😉', affection: '❤️', confidence: '⭐', neutral: '⚪',
  fear: '😨', surprise: '😲', disgust: '😒', contemplative: '🤔', excited: '🤩'
};

let emotionHistory = [];

export function init() {
  // Nothing needed
}

export function update(data) {
  // Mood
  if (data.mood) {
    const emotion = (data.mood.emotion || 'neutral').toLowerCase();
    const valence = data.mood.valence != null ? data.mood.valence : 0.5;

    const emojiEl = document.getElementById('mood-emoji');
    const labelEl = document.getElementById('mood-label');
    const fillEl = document.getElementById('mood-fill');

    if (emojiEl) emojiEl.textContent = EMOJI_MAP[emotion] || '⚪';
    if (labelEl) labelEl.textContent = emotion.toUpperCase();
    if (fillEl) fillEl.style.width = Math.round(valence * 100) + '%';

    // Track for emotion trace
    const emotionColors = {
      neutral: '#00d4ff', joy: '#22c55e', curiosity: '#a855f7', affection: '#ec4899',
      sadness: '#3b82f6', anger: '#ef4444', fear: '#f59e0b', surprise: '#06b6d4',
      confidence: '#22c55e', playful: '#d946ef'
    };
    emotionHistory.push({
      emotion, color: emotionColors[emotion] || '#00d4ff',
      intensity: data.mood.intensity || 0.5, time: Date.now()
    });
    if (emotionHistory.length > 50) emotionHistory.shift();
    drawEmotionTrace();
  }

  // Weather
  if (data.weather?.temp) {
    const iconEl = document.getElementById('weather-icon');
    const tempEl = document.getElementById('weather-temp');
    const detailEl = document.getElementById('weather-detail');

    let icon = '🌡';
    const cond = (data.weather.condition || '').toLowerCase();
    if (cond.includes('clear') || cond.includes('sunny')) icon = '☀️';
    else if (cond.includes('cloud') || cond.includes('overcast')) icon = '☁️';
    else if (cond.includes('rain') || cond.includes('drizzle')) icon = '🌧️';
    else if (cond.includes('storm') || cond.includes('thunder')) icon = '⛈️';
    else if (cond.includes('snow')) icon = '❄️';
    else if (cond.includes('fog') || cond.includes('mist')) icon = '🌫️';

    if (iconEl) iconEl.textContent = icon;
    if (tempEl) tempEl.textContent = data.weather.temp;
    if (detailEl) {
      const parts = [];
      if (data.weather.condition) parts.push(escapeHtml(data.weather.condition));
      if (data.weather.humidity) parts.push('H: ' + escapeHtml(data.weather.humidity));
      if (data.weather.wind) parts.push('W: ' + escapeHtml(data.weather.wind));
      detailEl.innerHTML = parts.join(' · ');
    }
  }
}

function drawEmotionTrace() {
  const canvas = document.getElementById('emotion-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  // Grid lines
  ctx.strokeStyle = 'rgba(0, 212, 255, 0.08)';
  ctx.lineWidth = 0.5;
  for (let y = 0; y < h; y += 12) {
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }

  if (emotionHistory.length < 2) return;
  const recent = emotionHistory.slice(-30);
  const step = w / Math.max(recent.length - 1, 1);

  // Intensity line
  ctx.beginPath();
  ctx.strokeStyle = 'rgba(0, 212, 255, 0.6)';
  ctx.lineWidth = 1.5;
  recent.forEach((entry, i) => {
    const x = i * step;
    const y = h - (entry.intensity * h * 0.8) - (h * 0.1);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Colored dots
  recent.forEach((entry, i) => {
    const x = i * step;
    const y = h - (entry.intensity * h * 0.8) - (h * 0.1);
    ctx.beginPath();
    ctx.fillStyle = entry.color;
    ctx.arc(x, y, 2, 0, Math.PI * 2);
    ctx.fill();
  });
}

export function destroy() { emotionHistory = []; }
