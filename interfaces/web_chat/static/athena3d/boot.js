/** Classic-script bridge; no CDN or import map required in the existing dashboard. */
(() => {
  const scriptURL=new URL(document.currentScript.src);
  const baseURL=new URL('./',scriptURL);
  const initialize=async()=>{
    let node=document.getElementById('avatar-canvas');
    if(!node)return;
    if(window.avatarRenderer?.getDiagnostics?.().version==='2.0.0')return;
    window.avatarRenderer?.destroy?.();
    if(node.tagName==='CANVAS'){
      const replacement=document.createElement('div');replacement.id=node.id;
      replacement.className=node.className;replacement.style.cssText=node.style.cssText;
      replacement.style.width='100%';replacement.style.height='100%';node.replaceWith(replacement);node=replacement;
    }
    const queue=[];let implementation=null;
    const methods=['setEmotion','setSpeaking','setState','setAttentionState','setGaze','setVisionTarget','updateFromChatMessage','processPhonemes','onStreamToken','setupCanvas'];
    const facade={getDiagnostics:()=>implementation?.getDiagnostics()??{version:'2.0.0',modelLoaded:false,verdict:'LOADING'},destroy:()=>implementation?.destroy()};
    for(const name of methods)facade[name]=(...args)=>implementation?implementation[name](...args):(queue.push([name,args]),queue.length>64&&queue.shift());
    window.avatarRenderer=facade;
    try{
      const {createAthena}=await import(new URL('athena.bundle.mjs',baseURL));
      implementation=createAthena(node,{baseURL});
      await implementation.ready;
      window.avatarRenderer=implementation;
      for(const [name,args]of queue)implementation[name](...args);
      dispatchEvent(new CustomEvent('athena:ready',{detail:implementation}));
    }catch(error){
      console.error('Athena 3D:',error);
      const message=document.createElement('p');message.className='avatar-error';message.textContent='3D avatar could not load: '+error.message;
      node.replaceChildren(message);dispatchEvent(new CustomEvent('athena:error',{detail:error.message}));
    }
  };
  document.readyState==='loading'?document.addEventListener('DOMContentLoaded',initialize,{once:true}):initialize();
})();
