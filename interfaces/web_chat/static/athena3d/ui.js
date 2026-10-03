(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const preview=new URLSearchParams(location.search).get('preview')==='1';
  let avatar=null;
  const report=error=>{$('message').textContent=error?.message??String(error);};
  const status=d=>{
    $('state').textContent=d.error?'Load error':String(d.state??'loading').replace(/^./,c=>c.toUpperCase());
    $('link').textContent=preview?'BROWSER PREVIEW':d.bridgeConnected?'CORETANA LINKED':'BRIDGE OFFLINE';
    $('link').dataset.online=String(d.bridgeConnected);
    $('fps').textContent=d.fps?`${d.fps} fps`:'Loading';
    $('model-name').textContent=d.model??'Loading real 3D model';
    $('audio-status').textContent=d.audioState==='running'?'Sound enabled':'Enable sound';
    $('diagnostics').textContent=JSON.stringify(d,null,2);
    if(d.error)report(d.error);
  };
  document.addEventListener('athena:status',event=>status(event.detail));
  addEventListener('athena:error',event=>report(event.detail));
  addEventListener('athena:ready',event=>{
    avatar=event.detail;status(avatar.getDiagnostics());
    $('message').textContent=preview?'Actual 3D renderer · no device connection':'Ready for Coretana events';
    for(const profile of avatar.profiles){const option=document.createElement('option');option.value=profile.id;option.textContent=profile.label;$('model-select').appendChild(option);}
    $('model-select').value=avatar.profile.id;
    if(!preview){
      const requested=new URLSearchParams(location.search).get('ws');
      const endpoint=requested??localStorage.getItem('athena.bridge.v2')??`${location.protocol==='https:'?'wss:':'ws:'}//${location.hostname}:9001`;
      $('bridge-url').value=endpoint;try{avatar.connect(endpoint);}catch(error){report(error);}
    }
  });
  $('settings-open').onclick=()=>$('settings').showModal();
  $('settings-close').onclick=()=>$('settings').close();
  $('fullscreen').onclick=()=>document.fullscreenElement?document.exitFullscreen():document.documentElement.requestFullscreen().catch(report);
  $('enable-sound').onclick=()=>avatar?.enableAudio().catch(report);
  $('connect').onclick=()=>{try{avatar.connect($('bridge-url').value);localStorage.setItem('athena.bridge.v2',$('bridge-url').value);}catch(error){report(error);}};
  $('disconnect').onclick=()=>avatar?.disconnect();
  $('model-select').onchange=()=>avatar?.loadProfile($('model-select').value).catch(report);
  $('state-select').onchange=()=>{avatar?.setState($('state-select').value);$('message').textContent='Manual animation test · no hardware action';};
  $('emotion-select').onchange=()=>avatar?.setEmotion($('emotion-select').value);
  $('stop').onclick=()=>avatar?.stopSpeech();
  $('audio-file').onchange=async()=>{
    const file=$('audio-file').files[0];if(file && avatar)try{await avatar.playAudio(await file.arrayBuffer());$('message').textContent='Mouth driven by the playing audio waveform';}catch(error){report(error);}
  };
  $('test-visemes').onclick=()=>{
    avatar?.processPhonemes({unit:'seconds',phonemes:['PP','aa','FF','I','O','U','sil'].map((viseme,i)=>({viseme,time:i*0.4,duration:0.35}))});
    $('message').textContent='Silent facial-rig test · not speech playback';
  };
  $('gaze-left').onclick=()=>avatar?.setVisionTarget({cx:0.15,cy:0.5,coordinateSpace:'normalized'});
  $('gaze-center').onclick=()=>avatar?.setVisionTarget({cx:0.5,cy:0.5,coordinateSpace:'normalized'});
  $('gaze-right').onclick=()=>avatar?.setVisionTarget({cx:0.85,cy:0.5,coordinateSpace:'normalized'});
  window.athenaDemo=(state='idle')=>{avatar?.setState(state);avatar?.setEmotion(state==='happy'?'happy':state==='alert'?'alert':'neutral');};
  addEventListener('pagehide',()=>avatar?.destroy(),{once:true});
})();
