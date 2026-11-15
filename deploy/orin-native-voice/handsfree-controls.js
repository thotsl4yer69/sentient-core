(function () {
  if (window.orinHandsfreeControls) return;
  window.orinHandsfreeControls = true;
  const mouthScript = document.createElement('script');
  mouthScript.src = '/static/js/handsfree-mouth.js';
  document.head.append(mouthScript);
  const labels = {wake_listening:'Waiting for wake phrase', listening:'Speak after the tone',
    transcribing:'Transcribing', thinking:'Thinking', synthesizing:'Preparing speech',
    speaking:'Speaking', muted:'Wake mic muted', waiting_for_usb:'Connect mic/speaker',
    waiting_for_audio:'Connect mic/speaker', recovering_audio:'Reconnecting microphone',
    retrying:'Voice retrying', cooldown:'Ready soon', unavailable:'Voice service unavailable', starting:'Starting voice', stopped:'Voice stopped'};
  const timer = setInterval(() => {
    const bar = document.getElementById('orin-airi-controls');
    if (!bar) return;
    clearInterval(timer);
    const group = document.createElement('div');
    group.id = 'orin-handsfree';
    group.style.cssText = 'display:flex;gap:7px;align-items:center;flex-wrap:wrap';
    const state = document.createElement('span');
    state.setAttribute('role','status');
    state.style.cssText = 'font-size:12px;max-width:190px';
    const button = document.createElement('button');
    button.type = 'button';
    button.style.cssText = 'padding:8px;border:1px solid #486176;border-radius:8px;background:#203448;color:#f4f8fc;cursor:pointer;white-space:nowrap';
    group.title = 'Hands-free voice. Say “Hey Honey”, wait for the tone, then speak. Wake detection, transcription and speech synthesis are local. Orin may use configured cloud fallback. Mute stops capture and spoken replies; it does not undo a tool action already sent.';
    group.append(state,button);
    bar.insertBefore(group,document.getElementById('orin-airi-more'));
    let current = false, busy = false, delay = 1000;
    async function refresh() {
      if (busy) return;
      try {
        const response = await fetch('/api/handsfree',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(4000)});
        if (!response.ok) throw Error('Voice status unavailable');
        const data = await response.json();
        current = data.enabled === true;
        const silent = current && data.capture_active &&
          ['wake_listening','listening'].includes(data.phase) &&
          data.input_signal?.state === 'digital_silence';
        state.textContent = silent ? 'Wake · microphone silent' :
          'Wake · ' + (data.phase === 'wake_listening' ? 'Say “' + (data.wake_phrase || 'wake phrase') + '”' : (labels[data.phase] || data.phase));
        state.title = data.error || '';
        state.style.color = silent ? '#ffb370' : '';
        group.dataset.inputSignal = data.input_signal?.state || 'unmeasured';
        button.textContent = current ? 'Mute wake mic' : 'Enable wake mic';
        button.setAttribute('aria-pressed',String(current));
        button.disabled = false;
        group.dataset.phase = data.phase;
        delay = ['transcribing','thinking','synthesizing','speaking','listening','cooldown'].includes(data.phase) ? 250 : 1000;
        window.dispatchEvent(new CustomEvent('orin-native-voice-state',{detail:data}));
      } catch (_) {
        state.textContent = 'Wake · status unavailable';
        button.textContent = 'Retry status';
        button.disabled = false;
        current = null;
        delay = 1500;
        window.dispatchEvent(new CustomEvent('orin-native-voice-state',{detail:{phase:'unavailable',enabled:false}}));
      }
    }
    button.onclick = async () => {
      if (current === null) { await refresh(); return; }
      busy = true; button.disabled = true;
      try {
        const response = await fetch('/api/handsfree',{method:'POST',credentials:'same-origin',
          headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!current}),signal:AbortSignal.timeout(4000)});
        if (!response.ok) throw Error();
        state.textContent = current ? 'Muting wake mic…' : 'Starting wake mic…';
      } catch (_) { state.textContent = 'Voice control failed · retry'; }
      finally { busy = false; setTimeout(refresh,400); }
    };
    async function poll() { await refresh(); setTimeout(poll,delay); }
    poll();
  },250);
})();
