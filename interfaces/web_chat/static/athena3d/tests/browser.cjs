/* Run against the complete release. Uses a local fixture, not the physical Orin. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
let playwright;
try{playwright=require('playwright');}catch{playwright=require('/usr/local/lib/node_modules/playwright');}
const root=path.resolve(__dirname,'..');
const out=path.resolve(process.env.ATHENA_EVIDENCE||path.join(root,'evidence'));
fs.mkdirSync(out,{recursive:true});
const types={'.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.json':'application/json','.glb':'model/gltf-binary'};
const server=http.createServer((request,response)=>{
  const pathname=decodeURIComponent(new URL(request.url,'http://localhost').pathname);
  const file=path.resolve(root,'.'+pathname);
  if(!file.startsWith(root+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()) {response.writeHead(404);return response.end();}
  response.writeHead(200,{'Content-Type':types[path.extname(file)]||'application/octet-stream'});
  fs.createReadStream(file).pipe(response);
});
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const origin='http://127.0.0.1:'+server.address().port;
 const browser=await playwright.chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--autoplay-policy=no-user-gesture-required']});
 const page=await browser.newPage({viewport:{width:800,height:480},deviceScaleFactor:1});
 const errors=[],requests=[],results=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>requests.push(request.url()));
 const check=async(name,fn)=>{try{await fn();results.push({name,pass:true});console.log('PASS',name);}catch(error){results.push({name,pass:false,error:error.message});console.log('FAIL',name,error.message);}};
 let socket;
 await page.routeWebSocket('ws://127.0.0.1:9010',ws=>{socket=ws;});
 const diagnostics=()=>page.evaluate(()=>window.avatarRenderer.getDiagnostics());
 try{
  await page.goto(origin+'/index.html?preview=1');
  await page.waitForFunction(()=>window.avatarRenderer?.getDiagnostics?.().modelLoaded,null,{timeout:60000});
  await check('real model and WebGL canvas load',async()=>{const d=await diagnostics();assert.equal(d.modelLoaded,true);assert.equal(d.bones,67);assert(d.facialShapes>=66);assert.equal(await page.locator('#avatar-canvas canvas').count(),1);});
  await check('runtime makes no external asset or cloud requests',async()=>assert(requests.every(url=>url.startsWith(origin)||url.startsWith('blob:')||url.startsWith('data:'))));
  await check('text alone does not animate imaginary speech',async()=>{await page.evaluate(()=>avatarRenderer.updateFromChatMessage('assistant','Testing text only'));assert.equal((await diagnostics()).isSpeaking,false);});
  await check('nested string false remains false',async()=>{await page.evaluate(()=>avatarRenderer.handleMessage({topic:'sentient/persona/speaking',payload:{active:'false'}}));assert.equal((await diagnostics()).isSpeaking,false);});
  await check('normalized vision centre looks straight ahead',async()=>{await page.evaluate(()=>avatarRenderer.setVisionTarget({cx:.5,cy:.5}));const d=await diagnostics();assert.equal(d.gaze.x,0);assert.equal(d.gaze.y,0);assert.equal(d.state,'seeing');});
  await check('pixel vision target uses frame dimensions',async()=>{await page.evaluate(()=>avatarRenderer.setVisionTarget({cx:600,cy:120,image_width:800,image_height:480}));const d=await diagnostics();assert.equal(d.gaze.x,.5);assert.equal(d.gaze.y,.5);});
  await check('native eye-close morph reaches closed',async()=>{await page.evaluate(()=>avatarRenderer.setState('sleep'));await page.waitForTimeout(700);assert((await page.evaluate(()=>avatarRenderer.head.getValue('eyesClosed')))>.5);});
  await check('waking releases closed-eye morph',async()=>{await page.evaluate(()=>avatarRenderer.setState('idle'));await page.waitForTimeout(700);assert((await page.evaluate(()=>avatarRenderer.head.getValue('eyesClosed')))<.1);});
  await check('timed viseme drives actual face geometry',async()=>{await page.evaluate(()=>avatarRenderer.processPhonemes({phonemes:[{viseme:'aa',time:0,duration:.7}]}));await page.waitForTimeout(280);assert.equal((await diagnostics()).activeViseme,'aa');assert((await page.evaluate(()=>avatarRenderer.head.getValue('viseme_aa')))>.1);});
  await check('timed facial animation ends and closes mouth',async()=>{await page.waitForTimeout(700);assert.equal((await diagnostics()).isSpeaking,false);assert.equal((await diagnostics()).activeViseme,'sil');});
  await page.evaluate(()=>{
   window.testWave=()=>{
    const rate=16000,count=rate*3,bytes=new ArrayBuffer(44+count*2),v=new DataView(bytes);
    const text=(n,s)=>{for(let i=0;i<s.length;i++)v.setUint8(n+i,s.charCodeAt(i));};
    text(0,'RIFF');v.setUint32(4,36+count*2,true);text(8,'WAVE');text(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,rate,true);v.setUint32(28,rate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);text(36,'data');v.setUint32(40,count*2,true);
    for(let i=0;i<count;i++){const t=i/rate;v.setInt16(44+i*2,Math.sin(2*Math.PI*220*t)*9000*(t>.1&&t<2.5?1:0),true);}return bytes;
   };
  });
  await check('actual audio waveform drives jaw movement',async()=>{await page.evaluate(()=>avatarRenderer.playAudio(testWave()));await page.waitForTimeout(650);assert.equal((await diagnostics()).lipSync,'audio-amplitude');assert((await page.evaluate(()=>avatarRenderer.head.getValue('jawOpen')))>.1);});
  await check('stopping speech cancels playback and releases mouth',async()=>{await page.evaluate(()=>avatarRenderer.stopSpeech());await page.waitForTimeout(250);assert.equal((await diagnostics()).isSpeaking,false);assert.equal(await page.evaluate(()=>avatarRenderer.audio),null);assert((await page.evaluate(()=>avatarRenderer.head.getValue('jawOpen')))<.1);});
  await check('timed lips use actual audio clock',async()=>{await page.evaluate(()=>avatarRenderer.playAudio(testWave(),{phonemes:[{viseme:'O',time:.1,duration:1}]}));await page.waitForTimeout(450);assert.equal((await diagnostics()).lipSync,'audio-clock-visemes');assert.equal((await diagnostics()).activeViseme,'O');await page.evaluate(()=>avatarRenderer.stopSpeech());});
  await check('bridge events update the real runtime',async()=>{await page.evaluate(()=>avatarRenderer.connect('ws://127.0.0.1:9010'));await page.waitForFunction(()=>avatarRenderer.connected);socket.send(JSON.stringify({topic:'sentient/conversation/thinking',payload:{is_thinking:true}}));await page.waitForFunction(()=>avatarRenderer.state==='thinking');});
  await check('bridge false event stops speaking',async()=>{socket.send(JSON.stringify({topic:'sentient/persona/speaking',payload:{active:'false'}}));await page.waitForFunction(()=>avatarRenderer.state==='idle');});
  await check('explicit disconnect closes the bridge',async()=>{await page.evaluate(()=>avatarRenderer.disconnect());assert.equal((await diagnostics()).bridgeConnected,false);assert.equal(await page.evaluate(()=>avatarRenderer.bridgeEnabled),false);});
  await check('settings and controls respond at kiosk size',async()=>{await page.getByRole('button',{name:'Avatar settings'}).click();assert(await page.locator('#settings').isVisible());await page.selectOption('#emotion-select','happy');assert.equal((await diagnostics()).currentEmotion,'happy');await page.getByRole('button',{name:'Close',exact:true}).click();});
  await page.evaluate(()=>{avatarRenderer.setState('idle');avatarRenderer.setEmotion('neutral');avatarRenderer.setGaze(0,0);});
  await page.waitForTimeout(800);
  await page.screenshot({path:path.join(out,'athena-800x480.png')});
  await check('responsive phone layout remains operable',async()=>{await page.setViewportSize({width:390,height:844});await page.waitForTimeout(500);const size=await page.locator('#avatar-canvas canvas').boundingBox();assert.equal(Math.round(size.width),390);assert.equal(Math.round(size.height),844);await page.getByRole('button',{name:'Avatar settings'}).click();assert(await page.locator('#settings').isVisible());await page.getByRole('button',{name:'Close',exact:true}).click();});
  await page.screenshot({path:path.join(out,'athena-phone.png')});
  await check('no uncaught JavaScript errors',async()=>assert.deepEqual(errors,[]));
  const report={environment:'Linux Chromium / SwiftShader software rendering; not physical Orin',checks:results,diagnostics:await diagnostics(),errors,externalRequests:requests.filter(url=>!url.startsWith(origin)&&!url.startsWith('blob:')&&!url.startsWith('data:'))};
  fs.writeFileSync(path.join(out,'browser-report.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({passed:results.filter(t=>t.pass).length,total:results.length,errors}));
  if(results.some(t=>!t.pass))process.exitCode=1;
 }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
