/** sc/v1 payloads and the TalkingHead speakAudio argument list. */

export const VISEMES = ['sil', 'PP', 'FF', 'TH', 'DD', 'kk', 'CH', 'SS', 'nn', 'RR', 'aa', 'E', 'I', 'O', 'U'];

export const MOODS = {
  neutral: 'neutral',
  calm: 'neutral',
  happy: 'happy',
  joy: 'happy',
  angry: 'angry',
  anger: 'angry',
  sad: 'sad',
  sadness: 'sad',
  fear: 'fear',
  disgust: 'disgust',
  love: 'love',
  affection: 'love',
  sleep: 'sleep',
};

export const GESTURES = new Set(['handup', 'index', 'ok', 'thumbup', 'thumbdown', 'side', 'shrug', 'namaste']);

export function speakPayload(pkg) {
  const durations = Array.isArray(pkg.vdurations) ? pkg.vdurations : [];
  const duration = durations.reduce((sum, value) => sum + Number(value || 0), 0) || 1;
  let pcm = pkg.pcm;
  if (!(pcm instanceof ArrayBuffer)) {
    if (ArrayBuffer.isView(pcm)) {
      pcm = pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength);
    } else {
      pcm = new ArrayBuffer(2);
    }
  }
  if (pcm.byteLength < 2) pcm = new ArrayBuffer(2);
  if (pcm.byteLength % 2) {
    const even = new ArrayBuffer(pcm.byteLength + 1);
    new Uint8Array(even).set(new Uint8Array(pcm));
    pcm = even;
  }
  return {
    audio: [pcm],
    words: [' '],
    wtimes: [0],
    wdurations: [duration],
    visemes: Array.isArray(pkg.visemes) ? pkg.visemes : [],
    vtimes: Array.isArray(pkg.vtimes) ? pkg.vtimes : [],
    vdurations: durations,
  };
}

export function moodName(value) {
  const key = String(value || '').trim().toLowerCase();
  return MOODS[key] || null;
}

/** Interpret one MQTT message. Returns an action or null. */
export function controlAction(topic, payload) {
  const name = String(topic || '');
  const body = payload && typeof payload === 'object' ? payload : {};
  if (name.endsWith('/turn/cancel') || name === 'turn/cancel') {
    return { type: 'cancel', turnId: String(body.turn_id || ''), reason: body.reason || 'barge-in' };
  }
  if (name.endsWith('/avatar/state') || name === 'avatar/state') {
    const gaze = body.gaze && typeof body.gaze === 'object' ? body.gaze : null;
    const gesture = typeof body.gesture === 'string' ? body.gesture : '';
    return {
      type: 'avatar',
      mood: moodName(body.mood || body.emotion),
      gaze: gaze ? { x: Number(gaze.x) || 0, y: Number(gaze.y) || 0 } : null,
      gesture: GESTURES.has(gesture) ? gesture : '',
    };
  }
  return null;
}

export function playbackMessage(turnId, state, source = 'sc-avatar-display') {
  return {
    turn_id: turnId,
    ts: new Date().toISOString(),
    source,
    state,
  };
}
