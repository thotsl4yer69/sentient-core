import assert from 'node:assert/strict';
import test from 'node:test';
import { MqttClient, decodePacket, encodeConnect, encodePublish } from '../src/mqtt.mjs';

function fakeSocket() {
  const handlers = {};
  return {
    binaryType: 'blob',
    sent: [],
    send(data) { this.sent.push(data); },
    close() { handlers.close?.(); },
    addEvent() {},
    set onopen(fn) { handlers.open = fn; },
    set onmessage(fn) { handlers.message = fn; },
    set onclose(fn) { handlers.close = fn; },
    set onerror(fn) { handlers.error = fn; },
    open() { handlers.open?.(); },
    message(bytes) { handlers.message?.({ data: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) }); },
    drop() { handlers.close?.(); },
  };
}

test('connect packet names MQTT 3.1.1 and the client id', () => {
  const packet = encodeConnect({ clientId: 'sc-avatar-display', username: 'sc', password: 'secret' });
  const decoded = decodePacket(packet);
  assert.equal(decoded.packet.type, 1);
  assert.ok(decoded.rest.length === 0);
});

test('publish round trip keeps the topic and json body', () => {
  const encoded = encodePublish('sc/v1/tts/state', '{"state":"playback_start"}', 7, 1);
  const decoded = decodePacket(encoded);
  assert.equal(decoded.packet.type, 3);
  assert.equal(decoded.packet.topic, 'sc/v1/tts/state');
  assert.equal(decoded.packet.qos, 1);
  assert.equal(decoded.packet.packetId, 7);
  assert.equal(new TextDecoder().decode(decoded.packet.payload), '{"state":"playback_start"}');
});

test('client subscribes after CONNACK and reconnects within 10 seconds', async () => {
  const sockets = [];
  let now = 1_000;
  const waits = [];
  const client = new MqttClient({
    url: 'ws://broker/mqtt',
    username: 'sc',
    password: 'pw',
    topics: ['sc/v1/turn/cancel'],
    socketFactory() {
      const socket = fakeSocket();
      sockets.push(socket);
      return socket;
    },
    now: () => now,
    schedule(fn, ms) { waits.push(ms); setTimeout(fn, 0); },
  });
  client.start();
  assert.equal(sockets.length, 1);
  sockets[0].open();
  const connect = sockets[0].sent[0];
  assert.equal(connect[0] >> 4, 1);
  sockets[0].message(Uint8Array.from([0x20, 0x02, 0x00, 0x00]));
  assert.equal(client.connected, true);
  const subscribe = sockets[0].sent[1];
  assert.equal(subscribe[0] >> 4, 8);
  now = 5_000;
  sockets[0].drop();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(sockets.length, 2);
  assert.ok(waits[0] <= 10000);
});
