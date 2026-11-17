/** MQTT 3.1.1 over a WebSocket. Enough for sc/v1 subscribe and QoS 1 publish. */

import { reconnectDelayMs } from './reconnect.mjs';

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function encodeVarInt(value) {
  const out = [];
  let n = value;
  do {
    let byte = n % 128;
    n = Math.floor(n / 128);
    if (n > 0) byte |= 0x80;
    out.push(byte);
  } while (n > 0);
  return out;
}

function utf8Field(text) {
  const bytes = encoder.encode(text);
  return [bytes.length >> 8, bytes.length & 0xff, ...bytes];
}

export function encodeConnect({ clientId, username = '', password = '', keepalive = 30 }) {
  const flags = (username ? 0x80 : 0) | (password ? 0x40 : 0) | 0x02;
  const body = [
    ...utf8Field('MQTT'),
    4,
    flags,
    (keepalive >> 8) & 0xff,
    keepalive & 0xff,
    ...utf8Field(clientId),
  ];
  if (username) body.push(...utf8Field(username));
  if (password) body.push(...utf8Field(password));
  return Uint8Array.from([0x10, ...encodeVarInt(body.length), ...body]);
}

export function encodeSubscribe(packetId, topic, qos = 1) {
  const body = [(packetId >> 8) & 0xff, packetId & 0xff, ...utf8Field(topic), qos & 0x03];
  return Uint8Array.from([0x82, ...encodeVarInt(body.length), ...body]);
}

export function encodePublish(topic, payload, packetId = 1, qos = 1) {
  const bytes = typeof payload === 'string' ? encoder.encode(payload) : payload;
  const body = [...utf8Field(topic)];
  if (qos > 0) body.push((packetId >> 8) & 0xff, packetId & 0xff);
  body.push(...bytes);
  const flags = qos === 1 ? 0x32 : 0x30;
  return Uint8Array.from([flags, ...encodeVarInt(body.length), ...body]);
}

export function encodePing() {
  return Uint8Array.from([0xc0, 0x00]);
}

function readVarInt(buf, offset) {
  let value = 0;
  let shift = 0;
  let pos = offset;
  for (let i = 0; i < 4; i += 1) {
    if (pos >= buf.length) return null;
    const byte = buf[pos];
    pos += 1;
    value += (byte & 0x7f) * 2 ** shift;
    if ((byte & 0x80) === 0) return { value, next: pos };
    shift += 7;
  }
  throw new Error('bad remaining length');
}

function readUtf8(buf, offset) {
  if (offset + 2 > buf.length) return null;
  const length = (buf[offset] << 8) | buf[offset + 1];
  const end = offset + 2 + length;
  if (end > buf.length) return null;
  return { text: decoder.decode(buf.subarray(offset + 2, end)), next: end };
}

export function decodePacket(buf) {
  if (buf.length < 2) return null;
  const remaining = readVarInt(buf, 1);
  if (!remaining) return null;
  const total = remaining.next + remaining.value;
  if (buf.length < total) return null;
  const type = buf[0] >> 4;
  const body = buf.subarray(remaining.next, total);
  const packet = { type, flags: buf[0] & 0x0f };
  if (type === 2) packet.returnCode = body[1];
  if (type === 3) {
    const topic = readUtf8(body, 0);
    let pos = topic.next;
    const qos = (buf[0] >> 1) & 0x03;
    let packetId = 0;
    if (qos > 0) {
      packetId = (body[pos] << 8) | body[pos + 1];
      pos += 2;
    }
    packet.topic = topic.text;
    packet.payload = body.subarray(pos);
    packet.qos = qos;
    packet.packetId = packetId;
  }
  if (type === 4) packet.packetId = (body[0] << 8) | body[1];
  return { packet, rest: buf.subarray(total) };
}

export class MqttClient {
  constructor(options) {
    this.url = options.url;
    this.username = options.username || '';
    this.password = options.password || '';
    this.clientId = options.clientId || 'sc-avatar-display';
    this.topics = options.topics || [];
    this.onMessage = options.onMessage || (() => {});
    this.onConnect = options.onConnect || (() => {});
    this.socketFactory = options.socketFactory || ((url) => new WebSocket(url));
    this.now = options.now || (() => Date.now());
    this.schedule = options.schedule || ((fn, ms) => setTimeout(fn, ms));
    this.attempt = 0;
    this.stopped = false;
    this.ws = null;
    this.connected = false;
    this.packetId = 1;
    this.buffer = new Uint8Array(0);
    this.closedAt = 0;
  }

  start() {
    this.stopped = false;
    this._open();
  }

  stop() {
    this.stopped = true;
    this.ws?.close();
  }

  publish(topic, payload, qos = 1) {
    if (!this.connected || !this.ws) return false;
    const id = this.packetId;
    this.packetId = (this.packetId % 65535) + 1;
    const text = typeof payload === 'string' ? payload : JSON.stringify(payload);
    this.ws.send(encodePublish(topic, text, id, qos));
    return true;
  }

  _open() {
    if (this.stopped) return;
    const ws = this.socketFactory(this.url);
    this.ws = ws;
    if ('binaryType' in ws) ws.binaryType = 'arraybuffer';
    ws.onopen = () => {
      const openedAt = this.now();
      if (this.closedAt) this.lastReconnectMs = openedAt - this.closedAt;
      this.attempt = 0;
      ws.send(encodeConnect({
        clientId: this.clientId,
        username: this.username,
        password: this.password,
      }));
    };
    ws.onmessage = (event) => {
      const chunk = event.data instanceof ArrayBuffer ? new Uint8Array(event.data) : new Uint8Array();
      const joined = new Uint8Array(this.buffer.length + chunk.length);
      joined.set(this.buffer, 0);
      joined.set(chunk, this.buffer.length);
      this.buffer = joined;
      this._drain();
    };
    ws.onclose = () => {
      this.connected = false;
      this.closedAt = this.now();
      if (this.stopped) return;
      const delay = reconnectDelayMs(this.attempt);
      this.attempt += 1;
      this.schedule(() => this._open(), delay);
    };
    ws.onerror = () => {};
  }

  _drain() {
    let rest = this.buffer;
    while (rest.length) {
      const decoded = decodePacket(rest);
      if (!decoded) break;
      rest = decoded.rest;
      this._handle(decoded.packet);
    }
    this.buffer = rest;
  }

  _handle(packet) {
    if (packet.type === 2 && packet.returnCode === 0) {
      this.connected = true;
      for (const topic of this.topics) {
        this.ws.send(encodeSubscribe(this.packetId, topic, 1));
        this.packetId = (this.packetId % 65535) + 1;
      }
      this.onConnect();
    }
    if (packet.type === 3) {
      let payload = decoder.decode(packet.payload);
      try { payload = JSON.parse(payload); } catch { /* leave text */ }
      this.onMessage(packet.topic, payload);
      if (packet.qos === 1 && packet.packetId) {
        const id = packet.packetId;
        this.ws.send(Uint8Array.from([0x40, 0x02, (id >> 8) & 0xff, id & 0xff]));
      }
    }
  }
}
