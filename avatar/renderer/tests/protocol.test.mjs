import assert from 'node:assert/strict';
import test from 'node:test';
import { controlAction, playbackMessage, speakPayload } from '../src/protocol.mjs';
import { reconnectDelayMs } from '../src/reconnect.mjs';

test('speakAudio payload carries visemes and a word so TalkingHead applies them', () => {
  const pcm = new ArrayBuffer(4);
  const payload = speakPayload({
    pcm,
    visemes: ['aa', 'PP'],
    vtimes: [0, 120],
    vdurations: [120, 80],
  });
  assert.deepEqual(payload.visemes, ['aa', 'PP']);
  assert.deepEqual(payload.vtimes, [0, 120]);
  assert.deepEqual(payload.vdurations, [120, 80]);
  assert.equal(payload.words.length, 1);
  assert.equal(payload.audio[0].byteLength, 4);
});

test('turn/cancel and avatar/state map onto renderer actions', () => {
  const cancel = controlAction('sc/v1/turn/cancel', { turn_id: 't1', reason: 'barge-in' });
  assert.deepEqual(cancel, { type: 'cancel', turnId: 't1', reason: 'barge-in' });
  const state = controlAction('sc/v1/avatar/state', {
    mood: 'happy',
    gaze: { x: 0.2, y: -0.4 },
    gesture: 'thumbup',
  });
  assert.equal(state.mood, 'happy');
  assert.deepEqual(state.gaze, { x: 0.2, y: -0.4 });
  assert.equal(state.gesture, 'thumbup');
  assert.equal(controlAction('sc/v1/avatar/state', { gesture: 'nope' }).gesture, '');
});

test('playback messages carry turn_id for the self-trigger guard', () => {
  const message = playbackMessage('t9', 'playback_start');
  assert.equal(message.turn_id, 't9');
  assert.equal(message.state, 'playback_start');
  assert.equal(message.source, 'sc-avatar-display');
  assert.ok(message.ts.includes('T'));
});

test('reconnect delay stays inside 10 seconds', () => {
  for (let attempt = 0; attempt < 12; attempt += 1) {
    const delay = reconnectDelayMs(attempt);
    assert.ok(delay > 0 && delay <= 8000, String(delay));
  }
});
