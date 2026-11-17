/** Hologram renderer. No chat UI. Black clear, TalkingHead, sc/v1. */

import * as THREE from 'three';
import { TalkingHead } from '../vendor/talkinghead.mjs';
import { MqttClient } from './mqtt.mjs';
import { controlAction, playbackMessage, speakPayload, VISEMES } from './protocol.mjs';
import { reconnectDelayMs } from './reconnect.mjs';

const VISEME_KEYS = VISEMES.map((name) => `viseme_${name}`);

function clamp(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.max(-1, Math.min(1, n));
}

class AvatarApp {
  constructor(root, config) {
    this.root = root;
    this.config = config;
    this.head = null;
    this.loaded = false;
    this.error = '';
    this.turnId = '';
    this.turns = 0;
    this.stuck = false;
    this.fps = 0;
    this.leadMs = null;
    this.lastCancelMs = null;
    this.lastReconnectS = null;
    this.playing = false;
    this.playAt = 0;
    this.audioAt = 0;
    this.mouthAt = 0;
    this._frames = 0;
    this._frameStamp = performance.now();
    this.mqtt = null;
    this.stream = null;
    this.ready = this._boot();
  }

  async _boot() {
    const mirror = this.config.mirror !== false;
    this.root.style.transform = mirror ? 'scaleX(-1)' : '';
    this.head = new TalkingHead(this.root, {
      lipsyncModules: [],
      lipsyncLang: 'en',
      ttsEndpoint: null,
      cameraView: 'head',
      cameraRotateEnable: false,
      cameraPanEnable: false,
      cameraZoomEnable: false,
      modelFPS: 30,
      modelPixelRatio: 1,
      avatarIdleEyeContact: 0.8,
      avatarIdleHeadMove: 0.18,
      avatarSpeakingEyeContact: 0.7,
      avatarSpeakingHeadMove: 0.12,
      lightAmbientColor: 0x0a0a0a,
      lightAmbientIntensity: 0.35,
      lightDirectColor: 0xd7e4ff,
      lightDirectIntensity: 6,
      lightDirectPhi: 1.15,
      lightDirectTheta: 2.6,
      lightSpotColor: 0xfff2df,
      lightSpotIntensity: 12,
      lightSpotPhi: 2.35,
      lightSpotTheta: Math.PI,
      lightSpotDispersion: 0.55,
      pcmSampleRate: this.config.sampleRate || 22050,
      update: (dt) => this._frame(dt),
    });
    await this.head.showAvatar({
      url: new URL(this.config.modelUrl || 'models/mpfb.glb', location.href).href,
      body: this.config.body || 'F',
      avatarMood: 'neutral',
      lipsyncLang: 'en',
    });
    this.head.renderer.setClearColor(0x000000, 1);
    this.head.scene.background = new THREE.Color(0x000000);
    this.head.renderer.shadowMap.enabled = false;
    this.head.setView('head');
    try { await this.head.audioCtx.resume(); } catch { /* kiosk policy resumes on start */ }
    this.loaded = true;
    this._connectMqtt();
    this._connectStream();
    this._overlay(true);
    return this;
  }

  clear() {
    const color = new THREE.Color();
    this.head.renderer.getClearColor(color);
    return {
      hex: color.getHexString(),
      alpha: this.head.renderer.getClearAlpha(),
      background: this.head.scene.background ? this.head.scene.background.getHexString() : '',
    };
  }

  mouthLevels() {
    const levels = {};
    for (const key of VISEME_KEYS) {
      let max = 0;
      for (const mesh of this.head?.morphs || []) {
        const index = mesh.morphTargetDictionary?.[key];
        if (index !== undefined) max = Math.max(max, mesh.morphTargetInfluences[index] || 0);
      }
      levels[key] = max;
    }
    return levels;
  }

  mouthOpen() {
    const levels = this.mouthLevels();
    return Object.entries(levels).some(([key, value]) => key !== 'viseme_sil' && value > 0.05);
  }

  playPackage(pkg) {
    if (!this.loaded) throw new Error('avatar is not loaded');
    const turnId = String(pkg.turn_id || pkg.turnId || '');
    this.head.stopSpeaking();
    this.head.opt.pcmSampleRate = pkg.sample_rate || pkg.sampleRate || this.config.sampleRate || 22050;
    this.turnId = turnId;
    this.turns += 1;
    this.playing = true;
    this.playAt = performance.now();
    this.audioAt = 0;
    this.mouthAt = 0;
    this.leadMs = null;
    this.stuck = false;
    this.head.speakAudio(speakPayload(pkg));
  }

  cancel(turnId = '') {
    const started = performance.now();
    if (turnId && this.turnId && turnId !== this.turnId) {
      this.lastCancelMs = performance.now() - started;
      return this.lastCancelMs;
    }
    const active = this.turnId;
    try { this.head.stopSpeaking(); } catch { /* already stopped */ }
    try { this.head.streamStop(); } catch { /* stream was not started */ }
    this._zeroMouth();
    if (this.playing && active) this._publishPlayback('playback_end', active);
    this.playing = false;
    this.turnId = '';
    this.lastCancelMs = performance.now() - started;
    this._overlay();
    return this.lastCancelMs;
  }

  applyAvatar(action) {
    if (!this.loaded || !action) return;
    if (action.mood) {
      try { this.head.setMood(action.mood); } catch { /* unknown mood stays */ }
    }
    if (action.gaze) this._gaze(action.gaze.x, action.gaze.y);
    if (action.gesture) {
      try { this.head.playGesture(action.gesture, 2.5, false, 600); } catch { /* rig has no pose */ }
    }
  }

  measureSample() {
    return {
      fps: this.fps,
      lead_ms: this.leadMs,
      cancel_ms: this.lastCancelMs,
      turns: this.turns,
      stuck: this.stuck,
      reconnect_s: this.lastReconnectS,
      loaded: this.loaded,
      turn_id: this.turnId,
    };
  }

  _gaze(x, y) {
    const rect = this.root.getBoundingClientRect();
    const px = rect.left + (clamp(x) + 1) * rect.width / 2;
    const py = rect.top + (1 - clamp(y)) * rect.height / 2;
    this.head.lookAt(px, py, 700);
  }

  _zeroMouth() {
    if (this.head?.resetLips) this.head.resetLips();
    for (const mesh of this.head?.morphs || []) {
      for (const key of VISEME_KEYS) {
        const index = mesh.morphTargetDictionary?.[key];
        if (index !== undefined) mesh.morphTargetInfluences[index] = 0;
      }
    }
    this.head?.render?.();
  }

  _frame() {
    const now = performance.now();
    this._frames += 1;
    if (now - this._frameStamp >= 1000) {
      this.fps = Math.round(this._frames * 1000 / (now - this._frameStamp));
      this._frames = 0;
      this._frameStamp = now;
      this._overlay();
    }
    const audible = Boolean(this.head?.isAudioPlaying);
    if (audible && !this.audioAt) {
      this.audioAt = now;
      this._publishPlayback('playback_start', this.turnId);
    }
    if (!this.mouthAt && this.mouthOpen()) this.mouthAt = now;
    if (this.audioAt && this.mouthAt && this.leadMs === null) {
      this.leadMs = Math.round(this.mouthAt - this.audioAt);
    }
    if (this.playing && this.audioAt && !audible && !this.head?.isSpeaking) {
      this._publishPlayback('playback_end', this.turnId);
      this.playing = false;
      this.turnId = '';
      setTimeout(() => {
        if (!this.turnId && this.mouthOpen()) this.stuck = true;
      }, 400);
    }
  }

  _publishPlayback(state, turnId) {
    if (!turnId || !this.mqtt) return;
    const prefix = (this.config.topicPrefix || 'sc/v1').replace(/\/$/, '');
    this.mqtt.publish(`${prefix}/tts/state`, playbackMessage(turnId, state));
  }

  _connectMqtt() {
    if (!this.config.brokerUrl) return;
    const prefix = (this.config.topicPrefix || 'sc/v1').replace(/\/$/, '');
    this.mqtt = new MqttClient({
      url: this.config.brokerUrl,
      username: this.config.mqttUsername || '',
      password: this.config.mqttPassword || '',
      clientId: 'sc-avatar-display',
      topics: [`${prefix}/avatar/state`, `${prefix}/turn/cancel`],
      onConnect: () => {
        if (this.mqtt.lastReconnectMs != null) this.lastReconnectS = this.mqtt.lastReconnectMs / 1000;
      },
      onMessage: (topic, payload) => {
        const action = controlAction(topic, payload);
        if (!action) return;
        if (action.type === 'cancel') this.cancel(action.turnId);
        else this.applyAvatar(action);
      },
    });
    this.mqtt.start();
  }

  _connectStream() {
    if (!this.config.streamUrl || !this.config.streamToken) return;
    let attempt = 0;
    let stopped = false;
    let header = null;
    const open = () => {
      if (stopped) return;
      const ws = new WebSocket(this.config.streamUrl);
      ws.binaryType = 'arraybuffer';
      this.stream = ws;
      ws.onopen = () => {
        attempt = 0;
        ws.send(JSON.stringify({ type: 'auth', role: 'display', token: this.config.streamToken }));
      };
      ws.onmessage = (event) => {
        if (typeof event.data === 'string') {
          let message;
          try { message = JSON.parse(event.data); } catch { return; }
          if (message.type === 'turn') header = message;
          if (message.type === 'cancel') this.cancel(message.turn_id || '');
          return;
        }
        if (header && event.data instanceof ArrayBuffer) {
          const pkg = header;
          header = null;
          this.playPackage({ ...pkg, pcm: event.data });
        }
      };
      ws.onclose = () => {
        if (stopped) return;
        const delay = reconnectDelayMs(attempt);
        attempt += 1;
        setTimeout(open, delay);
      };
    };
    open();
    this.stopStream = () => { stopped = true; this.stream?.close(); };
  }

  _overlay(force = false) {
    const node = document.getElementById('debug');
    if (!node) return;
    const enabled = this.config.debug === true || new URLSearchParams(location.search).has('debug');
    node.style.display = enabled ? 'block' : 'none';
    if (!enabled && !force) return;
    if (!enabled) return;
    const lead = this.leadMs === null ? '-' : `${this.leadMs} ms`;
    const cancel = this.lastCancelMs === null ? '-' : `${this.lastCancelMs.toFixed(1)} ms`;
    node.textContent = `fps ${this.fps}  lead ${lead}  cancel ${cancel}  turns ${this.turns}`;
  }
}

async function loadConfig() {
  const response = await fetch(new URL('config.json', location.href), { cache: 'no-store' });
  if (!response.ok) throw new Error(`config HTTP ${response.status}`);
  return response.json();
}

const root = document.getElementById('stage');
const app = new AvatarApp(root, await loadConfig());
window.__avatar = app;
app.ready.catch((error) => {
  app.error = error.message || String(error);
  console.error(app.error);
});

export { AvatarApp, reconnectDelayMs };
