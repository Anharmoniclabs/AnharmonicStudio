'use strict';
const token = location.hash.slice(1) || sessionStorage.getItem('studio-companion-token') || '';
sessionStorage.setItem('studio-companion-token', token);
history.replaceState(null, '', location.pathname);
const headers = {Authorization: `Bearer ${token}`};
const root = document.querySelector('#surfaces'), status = document.querySelector('#status');
const views = new Map(), pending = [];
let sending = false;
const modifiers = e => ({ctrl:e.ctrlKey, shift:e.shiftKey, alt:e.altKey, meta:e.metaKey});

async function flush() {
  if (sending) return;
  sending = true;
  try {
    while (pending.length) {
      const event = pending.shift();
      const response = await fetch('/input', {method:'POST', headers:{...headers,'Content-Type':'application/json'},
        body:JSON.stringify(event), signal:AbortSignal.timeout(3000)});
      if (!response.ok) throw Error('Input connection interrupted; release keys and reconnect.');
    }
  } catch (error) {
    pending.length = 0;
    // Release held notes if a press succeeded but a following release failed.
    try { await fetch('/input', {method:'POST', headers:{...headers,'Content-Type':'application/json'},
      body:JSON.stringify({type:'release'}), signal:AbortSignal.timeout(1000)}); } catch {}
    status.textContent = error.message;
  } finally { sending = false; }
}
function input(surface, data) {
  const event = {surface, ...data}, last = pending[pending.length-1];
  if (data.type === 'move' && last?.type === 'move' && last.surface === surface) pending[pending.length-1] = event;
  else if (pending.length < 64) pending.push(event);
  else { pending.length=0; pending.push({type:'release'}); }
  flush();
}
function point(canvas,e) {
  const r=canvas.getBoundingClientRect();
  return {x:Math.max(0,Math.min(canvas.width-1,Math.round((e.clientX-r.left)*canvas.width/r.width))),
    y:Math.max(0,Math.min(canvas.height-1,Math.round((e.clientY-r.top)*canvas.height/r.height)))};
}
function view(s) {
  const section=document.createElement('section'), title=document.createElement('h2'), canvas=document.createElement('canvas');
  title.textContent=s.title; canvas.tabIndex=0; canvas.setAttribute('aria-label',s.title+' desktop surface');
  section.append(title,canvas);
  if(s.main) root.append(section); else { root.prepend(section); canvas.focus(); }
  for(const [dom,type] of [['pointerdown','down'],['pointerup','up'],['pointermove','move'],['dblclick','double']]) {
    canvas.addEventListener(dom,e=>{
      if(type==='move'&&!e.buttons) return;
      e.preventDefault(); canvas.focus();
      if(type==='down') canvas.setPointerCapture(e.pointerId);
      input(s.id,{type,...point(canvas,e),button:e.button,buttons:e.buttons,...modifiers(e)});
    });
  }
  canvas.addEventListener('pointercancel',()=>input(s.id,{type:'release'}));
  canvas.addEventListener('contextmenu',e=>{e.preventDefault();input(s.id,{type:'context',...point(canvas,e),...modifiers(e)});});
  canvas.addEventListener('wheel',e=>{e.preventDefault();input(s.id,{type:'wheel',...point(canvas,e),delta:-e.deltaY,...modifiers(e)});},{passive:false});
  const held=new Set();
  for(const type of ['keydown','keyup']) canvas.addEventListener(type,e=>{
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='v') return;
    e.preventDefault();
    if(type==='keydown') held.add(e.key); else held.delete(e.key);
    input(s.id,{type,key:e.key,repeat:e.repeat,...modifiers(e)});
  });
  canvas.addEventListener('blur',()=>{for(const key of held) input(s.id,{type:'keyup',key});held.clear();});
  canvas.addEventListener('paste',e=>{e.preventDefault();input(s.id,{type:'text',text:e.clipboardData.getData('text/plain').slice(0,4096)});});
  return {section,canvas,title};
}
async function refresh() {
  try {
    const response=await fetch('/surfaces',{headers,signal:AbortSignal.timeout(3000)});
    if(!response.ok) throw Error(response.status===401?'Open the complete private connection link from Studio.':'Connection failed');
    const surfaces=await response.json();
    for(const [id,v] of views) if(!surfaces.some(s=>s.id===id)){v.section.remove();views.delete(id);}
    for(const s of surfaces) {
      let v=views.get(s.id);
      if(!v){v=view(s);views.set(s.id,v);}
      const frame=await fetch('/frame/'+s.id,{headers,signal:AbortSignal.timeout(3000)});
      if(!frame.ok) continue;
      const bitmap=await createImageBitmap(await frame.blob());
      if(v.canvas.width!==s.width||v.canvas.height!==s.height){v.canvas.width=s.width;v.canvas.height=s.height;}
      v.canvas.getContext('2d').drawImage(bitmap,0,0); bitmap.close();
    }
    status.textContent='Connected · native workstation';
  } catch(error) {status.textContent=error.message;}
  setTimeout(refresh,100);
}
refresh();
