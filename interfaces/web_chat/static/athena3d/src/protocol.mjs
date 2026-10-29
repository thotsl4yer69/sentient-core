/** Data contracts shared by the avatar, bridge and regression tests. */
export const STATES = new Set(['idle', 'listening', 'thinking', 'speaking', 'seeing', 'alert', 'sleep']);
export function clamp(value, lo = -1, hi = 1) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(lo, Math.min(hi, n)) : 0;
}
export function booleanValue(value) {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value === 1;
  if (value && typeof value === 'object') {
    return booleanValue(value.active ?? value.speaking ?? value.is_speaking ?? value.is_thinking ?? value.thinking ?? value.value ?? false);
  }
  return ['true', '1', 'on', 'yes', 'speaking'].includes(String(value).toLowerCase());
}
export function normalizeState(state) {
  const s = String(state ?? 'idle').toLowerCase();
  const mapped = {processing:'thinking', focused:'thinking', standby:'sleep', sleeping:'sleep', ready:'idle', relaxed:'idle'}[s] ?? s;
  return STATES.has(mapped) ? mapped : 'idle';
}
export function visionGaze(target) {
  if (!target || typeof target !== 'object') throw new TypeError('A vision target object is required.');
  let x = target.cx ?? target.center_x ?? target.x;
  let y = target.cy ?? target.center_y ?? target.y;
  if (Array.isArray(target.bbox)) {
    if (target.bbox.length !== 4) throw new RangeError('bbox must be [left, top, right, bottom].');
    const [a,b,c,d] = target.bbox.map(Number);
    if (![a,b,c,d].every(Number.isFinite) || c < a || d < b) throw new RangeError('Invalid bounding box.');
    x = (a+c)/2; y = (b+d)/2;
  }
  x = Number(x); y = Number(y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) throw new TypeError('Vision coordinates must be finite.');
  const width = Number(target.image_width ?? target.frame_width);
  const height = Number(target.image_height ?? target.frame_height);
  const space = target.coordinateSpace ?? target.space ?? ((width > 0 && height > 0) || Math.abs(x) > 1 || Math.abs(y) > 1 ? 'pixels' : 'normalized');
  if (space === 'gaze') return {x:clamp(x), y:clamp(y)};
  if (space === 'pixels') {
    if (!(width > 0 && height > 0)) throw new RangeError('Pixel targets require image_width and image_height.');
    x /= width; y /= height;
  } else if (space !== 'normalized') throw new RangeError('Unknown coordinate space.');
  return {x:clamp(2*x-1), y:clamp(1-2*y)};
}
const VISEMES = ['sil','PP','FF','TH','DD','kk','CH','SS','nn','RR','aa','E','I','O','U'];
const PHONEMES = {
  p:'PP', b:'PP', m:'PP', f:'FF', v:'FF', th:'TH', dh:'TH', 'θ':'TH', 'ð':'TH',
  t:'DD', d:'DD', k:'kk', g:'kk', ch:'CH', jh:'CH', sh:'CH', zh:'CH', 'ʃ':'CH', 'ʒ':'CH',
  s:'SS', z:'SS', n:'nn', ng:'nn', l:'nn', 'ŋ':'nn', r:'RR', 'ɹ':'RR',
  a:'aa', aa:'aa', ah:'aa', ae:'aa', 'ɑ':'aa', 'ʌ':'aa', 'æ':'aa', 'ə':'aa',
  e:'E', eh:'E', ey:'E', 'ɛ':'E', i:'I', ih:'I', iy:'I', 'ɪ':'I', y:'I',
  o:'O', ow:'O', ao:'O', 'ɔ':'O', u:'U', uw:'U', uh:'U', 'ʊ':'U', w:'U',
  sil:'sil', sp:'sil', pau:'sil', rest:'sil'
};
export function visemeName(value) {
  if (typeof value === 'number' && Number.isInteger(value)) return VISEMES[value] ?? 'sil';
  const s = String(value ?? '').replace(/^viseme_/i, '').replace(/[012]$/,'');
  return VISEMES.find(v => v.toLowerCase() === s.toLowerCase()) ?? PHONEMES[s.toLowerCase()] ?? 'sil';
}
export function phonemeTimeline(data = {}) {
  if (!Array.isArray(data.phonemes)) return [];
  const unit = data.unit ?? 'seconds';
  if (!['seconds','milliseconds'].includes(unit)) throw new RangeError('Phoneme unit must be seconds or milliseconds.');
  const multiplier = unit === 'milliseconds' ? 0.001 : 1;
  return data.phonemes.slice(0, 20000).map(p => {
    if (!p || typeof p !== 'object') throw new TypeError('Each phoneme requires explicit timing.');
    const start = Number(p.time ?? p.start) * multiplier;
    const duration = Number(p.duration) * multiplier;
    if (!Number.isFinite(start) || !Number.isFinite(duration) || start < 0 || duration <= 0) throw new RangeError('Invalid phoneme timing.');
    return {start, end:start+duration, shape:visemeName(p.viseme ?? p.phoneme ?? p.symbol ?? p.value), strength:clamp(p.intensity ?? 0.8, 0, 1)};
  }).sort((a,b) => a.start-b.start);
}
export function decodeEvent(message) {
  if (!message || typeof message !== 'object' || Array.isArray(message)) return null;
  const topic = String(message.topic ?? message.type ?? '');
  const payload = message.payload ?? message.data ?? message;
  return {topic, payload};
}
