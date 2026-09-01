const $ = id => document.getElementById(id);

function originPattern(value){
  const u = new URL(value);
  return `${u.protocol}//${u.host}/*`;
}
function ago(ts){
  if(!ts) return 'never';
  const s=Math.max(0,Math.floor((Date.now()-ts)/1000));
  if(s<60)return `${s}s ago`; if(s<3600)return `${Math.floor(s/60)}m ago`; return `${Math.floor(s/3600)}h ago`;
}
async function renderStatus(extra=''){
  const s=await chrome.runtime.sendMessage({action:'status'});
  const el=$('status');
  let lines=[];
  if(s.tokenCaptured) lines.push(`Suno token: captured (${ago(s.lastCaptured)})`); else lines.push('Suno token: waiting — open/use suno.com');
  if(s.lastSync) lines.push(`Tasia sync: ${ago(s.lastSync)}${s.connectedUsername?` as ${s.connectedUsername}`:''}`);
  if(s.lastError) lines.push(`Error: ${s.lastError}`);
  if(extra) lines.push(extra);
  el.textContent=lines.join('\n'); el.className='status '+(s.lastError?'bad':(s.lastSync?'good':''));
}

chrome.storage.local.get(['tasiaUrl','connectorKey']).then(data=>{
  $('tasiaUrl').value=data.tasiaUrl||''; $('connectorKey').value=data.connectorKey||''; renderStatus();
});

$('save').onclick=async()=>{
  const url=$('tasiaUrl').value.trim().replace(/\/+$/,''); const key=$('connectorKey').value.trim();
  const el=$('status');
  if(!url||!key){el.textContent='Enter both Streamer URL and connector key.';el.className='status bad';return;}
  try{
    const pattern=originPattern(url);
    const granted=await chrome.permissions.request({origins:[pattern]});
    if(!granted) throw new Error('Permission to contact this Tasia Streamer URL was not granted');
    await chrome.storage.local.set({tasiaUrl:url,connectorKey:key,lastError:''});
    const result=await chrome.runtime.sendMessage({action:'syncNow'});
    if(!result?.ok && String(result?.error||'').includes('No Suno token')) await renderStatus('Saved. Now open Suno and play/browse something so a session request occurs.');
    else if(!result?.ok) throw new Error(result?.error||'Sync failed');
    else await renderStatus('Connected.');
  }catch(e){el.textContent=String(e.message||e);el.className='status bad';}
};
$('openSuno').onclick=()=>chrome.tabs.create({url:'https://suno.com/'});
