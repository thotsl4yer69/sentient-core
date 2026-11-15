const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function render(data) {
  const elements = [];
  let initialize;
  const bar = {insertBefore() {}};
  const document = {
    head: {append() {}},
    getElementById: id => id === 'orin-airi-controls' ? bar : null,
    createElement: () => {
      const element = {style: {}, dataset: {}, append() {}, setAttribute() {}};
      elements.push(element);
      return element;
    },
  };
  const context = {document, window: {dispatchEvent() {}},
    setInterval: callback => {initialize = callback; return 1;},
    clearInterval() {}, setTimeout() {},
    AbortSignal: {timeout() {}}, CustomEvent: class {},
    fetch: async () => ({ok: true, json: async () => data}),
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../handsfree-controls.js'), 'utf8'), context);
  initialize();
  await new Promise(resolve => setImmediate(resolve));
  return {state: elements[2], group: elements[1], button: elements[3]};
}

test('digital silence replaces the wake prompt with an actionable status', async () => {
  const {state} = await render({enabled: true, capture_active: true,
    phase: 'wake_listening', wake_phrase: 'Hey Honey',
    input_signal: {state: 'digital_silence'}, error: 'Check input or reconnect USB audio'});
  assert.equal(state.textContent, 'Wake · microphone silent');
  assert.equal(state.title, 'Check input or reconnect USB audio');
});

test('valid signal preserves the actual Hey Honey wake phrase', async () => {
  const {state} = await render({enabled: true, capture_active: true,
    phase: 'wake_listening', wake_phrase: 'Hey Honey', input_signal: {state: 'signal_present'}});
  assert.equal(state.textContent, 'Wake · Say “Hey Honey”');
});

test('muted input does not display stale silence as an active fault', async () => {
  const {state, button} = await render({enabled: false, capture_active: false,
    phase: 'muted', input_signal: {state: 'digital_silence'}});
  assert.equal(state.textContent, 'Wake · Wake mic muted');
  assert.equal(button.textContent, 'Enable wake mic');
});

test('USB switch has a readable recovery status', async () => {
  const {state} = await render({enabled: true, phase: 'recovering_audio'});
  assert.equal(state.textContent, 'Wake · Reconnecting microphone');
});
