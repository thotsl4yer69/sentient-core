/** Real rigged avatar. TalkingHead/Three.js are bundled locally by build.sh. */
import { TalkingHead } from '../vendor/talkinghead.mjs';
import { booleanValue, clamp, normalizeState, visionGaze, phonemeTimeline, decodeEvent } from './protocol.mjs';

const MOODS = {happy:'happy', joy:'happy', amused:'happy', affection:'love', affectionate:'love', curious:'neutral', curiosity:'neutral', thoughtful:'neutral', thinking:'neutral', focused:'neutral', concerned:'sad', sadness:'sad', alert:'neutral', protective:'neutral', anger:'angry', neutral:'neutral', calm:'neutral'};
const LIP_KEYS = ['jawOpen','mouthOpen','viseme_PP','viseme_FF','viseme_TH','viseme_DD','viseme_kk','viseme_CH','viseme_SS','viseme_nn','viseme_RR','viseme_aa','viseme_E','viseme_I','viseme_O','viseme_U'];

export class Athena3D {
  constructor(node, options = {}) {
    if (!(node instanceof HTMLElement)) throw new TypeError('Avatar container required.');
    this.node = node;
    this.baseURL = new URL(options.baseURL ?? './', location.href);
    this.options = options;
    this.state = 'idle'; this.emotion = 'neutral'; this.connected = false;
    this.loaded = false; this.error = null; this.destroyed = false;
    this.timeline = []; this.timelineEpoch = 0; this.externalSpeaking = false;
    this.audio = null; this.analyzer = null; this.audioEpoch = 0;
    this.lipMode = 'none'; this.activeViseme = 'sil'; this.gaze = {x:0,y:0};
    this.ws = null; this.retryTimer = null; this.retry = 0; this.bridgeEnabled = false;
    this.fps = 0; this.frames = 0; this.lastFrameCount = 0; this.metricEpoch = performance.now();
    this.generation = 0; this.audioGeneration = 0; this.failedMessages = 0;
    this.profiles = []; this.profile = null; this.audit = {};
    this.onPointer = event => {
      if (!this.loaded || performance.now() - (this.lastVision ?? 0) < 1800) return;
      const r = this.node.getBoundingClientRect();
      if (r.width && r.height) this.setGaze((event.clientX-r.left)/r.width*2-1, 1-(event.clientY-r.top)/r.height*2);
    };
    this.onVisibility = () => { if (this.head) document.hidden ? this.head.stop() : this.head.start(); };
    this.node.addEventListener('pointermove', this.onPointer, {passive:true});
    document.addEventListener('visibilitychange', this.onVisibility);
    this.ready = this.initialize();
  }
  notify() {
    this.node.dispatchEvent(new CustomEvent('athena:status', {bubbles:true, detail:this.getDiagnostics()}));
  }
  async initialize() {
    try {
      const response = await fetch(new URL('config.json', this.baseURL), {cache:'no-store'});
      if (!response.ok) throw new Error(`Avatar configuration HTTP ${response.status}`);
      this.config = await response.json();
      this.profiles = this.config.profiles ?? [];
      if (!this.profiles.length) throw new Error('No real model configured.');
      await this.loadProfile(this.config.defaultProfile ?? this.profiles[0].id);
      this.notify();
      return this;
    } catch (error) {
      this.error = error.message; this.notify(); throw error;
    }
  }
  createHead() {
    const head = new TalkingHead(this.node, {
      lipsyncModules:[], lipsyncLang:'en', ttsEndpoint:null, ttsApikey:null,
      modelPixelRatio:1, modelFPS:30, cameraView:'head', cameraRotateEnable:false,
      cameraPanEnable:false, cameraZoomEnable:false, avatarIdleEyeContact:0.85,
      avatarIdleHeadMove:0.12, avatarSpeakingEyeContact:0.85, avatarSpeakingHeadMove:0.14,
      lightAmbientColor:0xffffff, lightAmbientIntensity:0.8,
      lightDirectColor:0xffeddb, lightDirectIntensity:5, lightDirectPhi:0.8, lightDirectTheta:0.35,
      lightSpotColor:0x83c7db, lightSpotIntensity:3, lightSpotPhi:0.8, lightSpotTheta:3.8,
      update:() => this.frame()
    });
    return head;
  }
  async loadProfile(id) {
    const profile = this.profiles.find(p => p.id === id);
    if (!profile) throw new RangeError(`Unknown avatar profile: ${id}`);
    const generation = ++this.generation;
    this.stopSpeech(); this.loaded = false; this.error = null; this.notify();
    this.head?.dispose();
    this.node.replaceChildren();
    this.head = this.createHead();
    const head = this.head;
    try {
      await head.showAvatar({
        url:new URL(profile.url, this.baseURL).href, body:profile.body ?? 'F',
        avatarMood:'neutral', lipsyncLang:'en', avatarIdleHeadMove:0.12,
        avatarIdleEyeContact:0.85, avatarSpeakingHeadMove:0.14,
        ...(profile.options ?? {})
      });
      if (this.destroyed || generation !== this.generation) return;
      this.profile = profile;
      const shapes = new Set(); const bones = new Set(); let vertices=0, triangles=0, meshCount=0;
      head.armature.traverse(object => {
        if (object.isBone) bones.add(object.name);
        if (object.isMesh) {
          meshCount++;
          vertices += object.geometry.attributes.position?.count ?? 0;
          triangles += (object.geometry.index?.count ?? object.geometry.attributes.position?.count ?? 0)/3;
          for (const name of Object.keys(object.morphTargetDictionary ?? {})) shapes.add(name);
          if (profile.style === 'athena' && object.material && !Array.isArray(object.material) && /casualsuit/i.test(object.material.name)) {
            object.material.color.setHex(0x4b6172);
            object.material.roughness=0.78; object.material.metalness=0.02;
          }
        }
      });
      this.audit = {bones:bones.size, facialShapes:shapes.size, shapeNames:[...shapes], vertices, triangles:Math.round(triangles), meshCount};
      this.loaded = true; this.state = 'idle';
      head.setView('head', {cameraDistance:0.04, cameraY:0.015});
      head.lookAtCamera(10000);
      this.setEmotion('neutral');
      this.notify();
    } catch (error) {
      if (generation !== this.generation) return;
      this.error=`${profile.label}: ${error.message}`; this.loaded=false; this.notify(); throw error;
    }
  }
  setEmotion(emotion, intensity = 0.7) {
    this.emotion=String(emotion ?? 'neutral').toLowerCase();
    this.emotionIntensity=clamp(intensity,0,1);
    if (!this.loaded) return;
    let mood=MOODS[this.emotion] ?? 'neutral';
    if (!this.head.animMoods[mood]) mood='neutral';
    this.head.setMood(mood);
    if (this.state === 'sleep') this.head.setBaselineValue('eyesClosed',1);
    if (this.emotion === 'alert') {
      this.head.setBaselineValue('eyeWideLeft',0.18); this.head.setBaselineValue('eyeWideRight',0.18);
    }
    this.notify();
  }
  setState(state) {
    const previous=this.state;
    this.state=normalizeState(state);
    if (this.state !== 'speaking' && previous === 'speaking') this.stopSpeech(false);
    if (this.loaded) {
      this.head.setBaselineValue('eyesClosed',this.state === 'sleep' ? 1 : 0);
      this.head.isListening=this.state === 'listening';
      this.head.opt.avatarIdleHeadMove=this.state === 'sleep' ? 0 : 0.12;
      if (this.state === 'listening') this.head.lookAtCamera(10000);
    }
    this.notify();
  }
  setAttentionState(state) {
    this.setState({relaxed:'idle', focused:'thinking'}[state] ?? state);
  }
  setSpeaking(active) {
    this.externalSpeaking=booleanValue(active);
    if (!this.externalSpeaking) this.stopSpeech();
    else { this.state='speaking'; this.lipMode=this.timeline.length ? 'event-timed' : 'waiting-for-audio'; this.notify(); }
  }
  updateFromChatMessage(role) {
    // Text arrival is not proof of audio playback.
    if (role === 'user') this.setState('thinking');
    else if (role === 'assistant' && !this.audio && !this.externalSpeaking) this.setState('idle');
  }
  onStreamToken() { if (!this.audio && !this.externalSpeaking) this.setState('thinking'); }
  setGaze(x,y,focus=1) {
    this.gaze={x:clamp(x),y:clamp(y),focus:clamp(focus,0,1)};
    if (!this.loaded) return;
    const r=this.node.getBoundingClientRect();
    this.head.lookAt(r.left+(this.gaze.x+1)*r.width/2,r.top+(1-this.gaze.y)*r.height/2,1500);
  }
  setVisionTarget(target) {
    const gaze=visionGaze(target); this.lastVision=performance.now();
    this.setState('seeing'); this.setGaze(gaze.x,gaze.y,target.confidence ?? 1);
  }
  processPhonemes(data={}) {
    const timeline=phonemeTimeline(data);
    this.timeline=timeline;
    this.timelineEpoch=this.audio ? this.head.audioCtx.currentTime-this.audioEpoch : performance.now()/1000;
    if (timeline.length) { this.state='speaking'; this.lipMode=this.audio ? 'audio-clock-visemes' : 'event-timed'; }
    this.notify();
  }
  async enableAudio() {
    await this.ready;
    await this.head.audioCtx.resume();
    if (this.head.audioCtx.state !== 'running') throw new Error('Tap Enable sound on the display to unlock browser audio.');
    this.notify();
  }
  async playAudio(input, timing={}) {
    await this.ready;
    this.stopSpeech(false);
    const generation=++this.audioGeneration;
    await this.head.audioCtx.resume();
    if (this.head.audioCtx.state !== 'running') throw new Error('Sound is locked. Tap Enable sound first.');
    let bytes=input;
    if (typeof input === 'string' || input instanceof URL) {
      const response=await fetch(input);
      if (!response.ok) throw new Error(`Audio HTTP ${response.status}`);
      bytes=await response.arrayBuffer();
    }
    if (ArrayBuffer.isView(bytes)) bytes=bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength);
    if (!(bytes instanceof ArrayBuffer) || bytes.byteLength > 64*1024*1024) throw new TypeError('Expected an audio file no larger than 64 MiB.');
    const decoded=await this.head.audioCtx.decodeAudioData(bytes.slice(0));
    if (generation !== this.audioGeneration || this.destroyed) return;
    const context=this.head.audioCtx;
    this.audio=context.createBufferSource(); this.audio.buffer=decoded;
    this.analyzer=context.createAnalyser(); this.analyzer.fftSize=256;
    this.samples=new Float32Array(this.analyzer.fftSize);
    this.audio.connect(this.analyzer); this.analyzer.connect(context.destination);
    this.timeline=phonemeTimeline(timing); this.audioEpoch=context.currentTime+0.03;
    this.timelineEpoch=0; this.state='speaking'; this.externalSpeaking=false;
    this.lipMode=this.timeline.length ? 'audio-clock-visemes' : 'audio-amplitude';
    this.head.isSpeaking=true;
    this.audio.onended=()=>{if(generation===this.audioGeneration)this.stopSpeech();};
    this.audio.start(this.audioEpoch); this.notify();
  }
  async playBase64(data, timing={}) {
    if (typeof data !== 'string' || data.length > 90*1024*1024) throw new TypeError('Invalid audio payload.');
    const raw=atob(data.replace(/^data:[^,]+,/,''));
    return this.playAudio(Uint8Array.from(raw,c=>c.charCodeAt(0)), timing);
  }
  frame() {
    if (!this.loaded || this.destroyed) return;
    const now=performance.now();
    if (now-this.metricEpoch>=1000) {
      const frame=this.head.renderer?.info?.render?.frame ?? 0;
      this.fps=Math.round((frame-this.lastFrameCount)*1000/(now-this.metricEpoch));
      this.lastFrameCount=frame; this.metricEpoch=now; this.notify();
    }
    let shape='sil', strength=0, jaw=0;
    const elapsed=this.audio ? this.head.audioCtx.currentTime-this.audioEpoch : now/1000-this.timelineEpoch;
    if (this.timeline.length && elapsed>=0) {
      const phoneme=this.timeline.find(p=>elapsed>=p.start && elapsed<p.end);
      if (phoneme) {shape=phoneme.shape;strength=phoneme.strength;}
      if (elapsed>=this.timeline[this.timeline.length-1].end) {
        this.timeline=[];
        if (!this.audio) {this.externalSpeaking=false;this.state='idle';this.lipMode='none';}
      }
    } else if (this.audio && this.analyzer && elapsed>=0) {
      this.analyzer.getFloatTimeDomainData(this.samples);
      let energy=0; for(const sample of this.samples)energy+=sample*sample;
      jaw=clamp((Math.sqrt(energy/this.samples.length)-0.008)*4.0,0,0.7);
    }
    this.activeViseme=shape;
    for(const key of LIP_KEYS) {
      const value=key==='jawOpen' ? jaw : key===`viseme_${shape}` ? strength : 0;
      if (this.head.mtAvatar[key]) this.head.setValue(key,value);
    }
  }
  stopSpeech(resetState=true) {
    ++this.audioGeneration;
    if (this.audio) {this.audio.onended=null;try{this.audio.stop();}catch{}this.audio.disconnect();}
    this.analyzer?.disconnect();this.audio=null;this.analyzer=null;this.samples=null;
    this.timeline=[];this.externalSpeaking=false;this.activeViseme='sil';this.lipMode='none';
    if(this.loaded){this.head.stopSpeaking();for(const key of LIP_KEYS)if(this.head.mtAvatar[key])this.head.setValue(key,0);}
    if(resetState)this.state='idle';
    this.notify();
  }
  handleMessage(message) {
    const event=decodeEvent(message); if(!event)return false;
    const {topic,payload:p}=event;const object=p && typeof p==='object' ? p : {};
    if(topic.endsWith('persona/state') || topic==='state') {
      if(object.emotion)this.setEmotion(object.emotion,object.emotion_intensity??object.intensity??0.7);
      if(booleanValue(object.speaking))this.setSpeaking(true);
      else if(booleanValue(object.thinking))this.setState('thinking');
      else if(object.speaking!=null || object.thinking!=null)this.setSpeaking(false);
      if(object.attention)this.setGaze(object.attention.x,object.attention.y,object.attention.focus);
    } else if(/(?:emotion|expression)$/.test(topic))this.setEmotion(object.emotion??object.expression??object.value??p,object.intensity??0.7);
    else if(/phonemes?$/.test(topic))this.processPhonemes(object);
    else if(topic==='tts_audio') {
      if(object.audio)this.playBase64(object.audio,object).catch(error=>{this.error=error.message;this.notify();});
    } else if(topic.endsWith('tts/started'))this.setSpeaking(true);
    else if(/tts\/(?:completed|stop)$/.test(topic))this.setSpeaking(false);
    else if(topic.endsWith('speaking'))this.setSpeaking(p);
    else if(topic.endsWith('thinking'))this.setState(booleanValue(p)?'thinking':'idle');
    else if(topic.endsWith('attention'))this.setGaze(object.x,object.y,object.focus);
    else if(topic.includes('/wake/'))this.setState('listening');
    else if(topic.endsWith('avatar/state'))this.setState(object.state??object.value??p);
    else if(/vision|detection/.test(topic)) {
      const target=object.detections?.[0]??object.objects?.[0]??object;
      if(target.bbox || target.cx!=null || target.center_x!=null || target.x!=null) {
        this.setVisionTarget({...target,image_width:target.image_width??object.image_width??object.frame_width,image_height:target.image_height??object.image_height??object.frame_height});
      }
    } else return false;
    return true;
  }
  connect(url) {
    this.disconnect();
    const endpoint=new URL(url,location.href);
    if(!['ws:','wss:'].includes(endpoint.protocol))throw new RangeError('Bridge URL must use ws:// or wss://.');
    if(location.protocol==='https:' && endpoint.protocol!=='wss:')throw new Error('An HTTPS display requires a WSS bridge.');
    this.bridgeEnabled=true;this.bridgeURL=endpoint.href;this.retry=0;this.openBridge();
  }
  openBridge() {
    if(!this.bridgeEnabled || this.destroyed)return;
    const socket=new WebSocket(this.bridgeURL);this.ws=socket;
    socket.onopen=()=>{if(socket!==this.ws)return;this.connected=true;this.retry=0;this.notify();};
    socket.onmessage=event=>{
      if(socket!==this.ws || typeof event.data!=='string' || event.data.length>12*1024*1024)return;
      try{this.handleMessage(JSON.parse(event.data));}catch(error){this.failedMessages++;console.warn('Avatar event rejected:',error.message);}
    };
    socket.onerror=()=>{};
    socket.onclose=()=>{
      if(socket!==this.ws)return;this.connected=false;this.notify();
      if(this.bridgeEnabled)this.retryTimer=setTimeout(()=>this.openBridge(),Math.min(15000,800*2**this.retry++));
    };
  }
  disconnect() {
    this.bridgeEnabled=false;clearTimeout(this.retryTimer);this.retryTimer=null;
    const socket=this.ws;this.ws=null;if(socket){socket.onclose=null;socket.close();}
    this.connected=false;this.notify();
  }
  setupCanvas(){if(this.loaded)this.head.setView('head',{cameraDistance:0.04,cameraY:0.015});}
  getDiagnostics() {
    return {version:'2.0.0',renderer:'TalkingHead / Three.js WebGL',modelLoaded:this.loaded,
      model:this.profile?.label??null,modelId:this.profile?.id??null,license:this.profile?.license??null,
      state:this.state,currentEmotion:this.emotion,isSpeaking:this.state==='speaking',gaze:this.gaze,
      bridgeConnected:this.connected,fps:this.fps,lipSync:this.lipMode,activeViseme:this.activeViseme,
      audioState:this.head?.audioCtx?.state??'uninitialized',error:this.error,failedMessages:this.failedMessages,
      ...this.audit,verdict:this.error?'ERROR':this.loaded?'MODEL_LOADED':'LOADING'};
  }
  destroy() {
    this.destroyed=true;++this.generation;this.disconnect();this.stopSpeech();
    this.node.removeEventListener('pointermove',this.onPointer);document.removeEventListener('visibilitychange',this.onVisibility);
    this.head?.dispose();this.node.replaceChildren();this.loaded=false;
  }
}
export function createAthena(node, options) {return new Athena3D(node,options);}
