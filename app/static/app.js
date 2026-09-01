const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const enc = s => encodeURIComponent(String(s ?? '')).replace(/'/g,'%27');
const state = {
  user:null, needsSetup:false, status:null, queue:[], playlist:[],
  libraryView:{path:'',parent:'',search:'',folders:[],tracks:[],stats:{}},
  libraryPath:'', favorites:[], sources:[], sourceRows:[], sourcePath:'', sourceId:null, sourceSearch:'', catalogResults:[], catalogSettings:{}, drag:null, nowReceived:0
};

async function api(path, opts={}) {
  const r = await fetch(path, {credentials:'same-origin', ...opts, headers:{...(opts.headers||{})}});
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) {
    if (r.status === 401 && !path.startsWith('/api/auth/')) showAuth(false);
    throw new Error(data?.detail || data || `HTTP ${r.status}`);
  }
  return data;
}
function jsonOpts(method, body) { return {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}; }
function sec(v) {
  v=Math.max(0,Math.round(Number(v)||0));
  const h=Math.floor(v/3600), m=Math.floor((v%3600)/60), s=v%60;
  return h ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}` : `${m}:${String(s).padStart(2,'0')}`;
}
function clock(epoch) { if(!epoch)return '—'; return new Date(epoch*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}); }
function trackActions(buttons) { return `<div class="track-actions">${buttons.join('')}</div>`; }

function showAuth(setup) {
  state.needsSetup=setup;
  $('appShell').classList.add('hidden'); $('authScreen').classList.remove('hidden');
  $('authTitle').textContent=setup?'Create first account':'Login';
  $('authHint').textContent=setup?'This first account becomes administrator and adopts your v1.x library/queue/playlist.':'Login to your own radio workspace.';
  $('authDisplay').classList.toggle('hidden',!setup);
  $('authSubmit').textContent=setup?'Create Tasia account':'Login';
}
function showApp() {
  $('authScreen').classList.add('hidden'); $('appShell').classList.remove('hidden');
  $('whoami').textContent=state.user.display_name||state.user.username;
  $('adminUsers').classList.toggle('hidden',!state.user.is_admin);
  const r=state.user.music_folder||'';
  $('musicRoot').textContent=r.startsWith('/music/')?'.'+r:(r||'Your private music folder');
}
async function boot() {
  const st=await api('/api/auth/setup-status');
  if(st.needs_setup){showAuth(true);return;}
  try { state.user=await api('/api/me'); showApp(); if(location.pathname==='/login') history.replaceState(null,'','/app'); await initialLoad(); }
  catch { showAuth(false); }
}
$('authSubmit').onclick=async()=>{
  const body={username:$('authUser').value.trim(),password:$('authPass').value,display_name:$('authDisplay').value.trim()||null};
  $('authMsg').textContent='';
  try { const r=await api(state.needsSetup?'/api/auth/setup':'/api/auth/login',jsonOpts('POST',body)); state.user=r.user; showApp(); history.replaceState(null,'','/app'); await initialLoad(); }
  catch(e){ $('authMsg').className='msg bad'; $('authMsg').textContent=e.message; }
};
$('authPass').addEventListener('keydown',e=>{if(e.key==='Enter')$('authSubmit').click();});
$('logoutBtn').onclick=async()=>{await api('/api/auth/logout',{method:'POST'});location.href='/login';};

async function initialLoad() {
  state.libraryPath=''; $('librarySearch').value='';
  await Promise.all([loadLibrary(),loadFavorites(),loadSources(),refreshAll()]);
}

// Local private library browser ------------------------------------------------
async function loadLibrary() {
  const q=$('librarySearch').value.trim();
  const qs=new URLSearchParams({folder:state.libraryPath});
  if(q) qs.set('q',q);
  try {
    state.libraryView=await api(`/api/library/browse?${qs}`);
    renderLibrary();
  } catch(e) {
    $('library').innerHTML=`<div class="empty">${esc(e.message)}</div>`;
  }
}
function renderLibrary() {
  const v=state.libraryView||{}, q=v.search||'';
  $('libraryPath').textContent=q?`Search: “${q}”`:`/${v.path||''}`;
  $('libraryUp').disabled=!v.path||!!q;
  const folderRows=(v.folders||[]).map(f=>{
    const encoded=enc(f.path);
    return `<div class="track folder-row" data-folder="${esc(f.path)}">
      <div class="track-main folder-open" onclick="openLibraryFolder('${encoded}')">
        <div class="track-title"><strong>📁 ${esc(f.name)}</strong></div>
        <div class="track-meta"><span>${Number(f.tracks)||0} tracks</span><span>${sec(f.seconds)}</span><span>includes subfolders</span></div>
      </div>
      ${trackActions([
        `<button title="Queue whole folder" onclick="folderBulk(event,'queue','${encoded}')">+Q</button>`,
        `<button class="ghost" title="Add whole folder to playlist" onclick="folderBulk(event,'playlist','${encoded}')">+P</button>`
      ])}
    </div>`;
  });
  const trackRows=(v.tracks||[]).map(t=>`<div class="track" data-id="${t.id}">
    <div class="track-main"><div class="track-title"><strong>${esc(t.title)}</strong></div>
      <div class="track-meta"><span>${esc(t.artist||'Unknown artist')}</span><span>${sec(t.duration)}</span><span>📁 ${esc((t.folder||'/').replace(/^\/$/,'Root'))}</span></div>
    </div>
    ${trackActions([`<button class="ghost preview-btn" title="Play locally in this browser only" onclick="previewLibrary(${t.id},'${enc(t.title)}','${enc(t.artist||'Unknown artist')}')">▶ Test</button>`,`<button onclick="queueLibrary(${t.id})">Q</button>`,`<button class="ghost" onclick="playlistLibrary(${t.id})">P</button>`,`<button class="ghost star-btn" title="Save to favourites" onclick="favoriteLibrary(${t.id})">★</button>`])}
  </div>`);
  $('library').innerHTML=[...folderRows,...trackRows].join('')||'<div class="empty">No songs or folders here. Add files and press Scan.</div>';
  const stats=v.stats||{};
  $('librarySummary').textContent=q?`${stats.tracks||0} matches · ${sec(stats.seconds)}`:`${stats.tracks||0} tracks in subtree · ${sec(stats.seconds)}`;
  $('queueLibraryAll').textContent=q?'+ Queue results':'+ Queue folder';
  $('playlistLibraryAll').textContent=q?'+ Results → Playlist':'+ Folder → Playlist';
}
window.openLibraryFolder=p=>{ state.libraryPath=decodeURIComponent(p); $('librarySearch').value=''; loadLibrary(); };
window.folderBulk=async(e,target,p)=>{ e?.stopPropagation(); const folder=decodeURIComponent(p); await bulkLibrary(target,folder,''); };
$('libraryUp').onclick=()=>{ if(!state.libraryPath)return; state.libraryPath=state.libraryPath.split('/').slice(0,-1).join('/'); loadLibrary(); };
$('libraryRoot').onclick=()=>{ state.libraryPath=''; $('librarySearch').value=''; loadLibrary(); };
$('clearSearch').onclick=()=>{ $('librarySearch').value=''; loadLibrary(); $('librarySearch').focus(); };
let searchTimer;
$('librarySearch').oninput=()=>{ clearTimeout(searchTimer); searchTimer=setTimeout(loadLibrary,180); };
window.queueLibrary=async id=>{ await api('/api/queue/library',jsonOpts('POST',{track_id:id})); refreshAll(); };
window.playlistLibrary=async id=>{ await api('/api/playlist/library',jsonOpts('POST',{track_id:id})); refreshAll(); };
window.favoriteLibrary=async id=>{try{await api('/api/favorites/library',jsonOpts('POST',{track_id:id}));await loadFavorites();}catch(e){alert(e.message)}};
async function playPrivatePreview(url,titleEnc,artistEnc){
  const box=$('localPreview'), audio=$('localPreviewAudio'), msg=$('localPreviewMsg');
  $('localPreviewTitle').textContent=decodeURIComponent(titleEnc||'')||'Local preview';
  $('localPreviewArtist').textContent=decodeURIComponent(artistEnc||'')||'Unknown artist';
  msg.className='msg'; msg.textContent='Browser preview only — radio output is untouched.';
  box.classList.remove('hidden');
  audio.pause();
  audio.src=`${url}${url.includes('?')?'&':'?'}v=${Date.now()}`;
  audio.load();
  try{await audio.play();}
  catch(e){msg.className='msg bad';msg.textContent='Preview could not start automatically. Press ▶ in the preview player.';}
}
window.previewLibrary=(id,titleEnc,artistEnc)=>playPrivatePreview(`/api/library/preview/${Number(id)}`,titleEnc,artistEnc);
window.previewQueue=(id,titleEnc,artistEnc)=>playPrivatePreview(`/api/queue/${Number(id)}/preview`,titleEnc,artistEnc);
window.previewPlaylist=(id,titleEnc,artistEnc)=>playPrivatePreview(`/api/playlist/${Number(id)}/preview`,titleEnc,artistEnc);
function stopLocalPreview(){
  const audio=$('localPreviewAudio');
  audio.pause();
  try{audio.currentTime=0;}catch{}
  audio.removeAttribute('src'); audio.load();
  $('localPreview').classList.add('hidden');
  $('localPreviewMsg').textContent='';
}
$('localPreviewStop').onclick=stopLocalPreview;
$('localPreviewAudio').addEventListener('error',()=>{
  if(!$('localPreviewAudio').getAttribute('src'))return;
  $('localPreviewMsg').className='msg bad';
  $('localPreviewMsg').textContent='Browser could not decode this file format. The radio can still use it if Liquidsoap/FFmpeg supports it.';
});
async function bulkLibrary(target,folder=state.libraryPath,q=$('librarySearch').value.trim()) {
  try {
    const endpoint=target==='queue'?'/api/library/bulk/queue':'/api/library/bulk/playlist';
    const r=await api(endpoint,jsonOpts('POST',{folder,q,recursive:true}));
    await refreshAll();
    const n=target==='queue'?r.queued:r.added;
    const where=q?`search “${q}”`:`/${folder||''}`;
    alert(`${target==='queue'?'Queued':'Added'} ${n} tracks from ${where}.`);
  } catch(e) { alert(e.message); }
}
$('scan').onclick=async()=>{ try{await api('/api/library/scan',{method:'POST'});await loadLibrary();}catch(e){alert(e.message)} };
$('uploadOpen').onclick=()=>$('uploadInput').click();
$('uploadInput').onchange=async()=>{
  const f=$('uploadInput').files[0];if(!f)return;
  const fd=new FormData();fd.append('file',f);
  try{await api('/api/upload',{method:'POST',body:fd});await loadLibrary();}catch(e){alert(e.message)}finally{$('uploadInput').value='';}
};
$('queueLibraryAll').onclick=()=>bulkLibrary('queue');
$('playlistLibraryAll').onclick=()=>bulkLibrary('playlist');

// Saved / favourites -----------------------------------------------------------
async function loadFavorites(){
  if(!state.user)return;
  const q=$('favoritesSearch')?.value.trim()||'';
  try{state.favorites=await api('/api/favorites'+(q?`?q=${encodeURIComponent(q)}`:''));renderFavorites();}
  catch(e){if($('favorites'))$('favorites').innerHTML=`<div class="empty">${esc(e.message)}</div>`;}
}
function renderFavorites(){
  if(!$('favorites'))return;
  const rows=state.favorites||[];
  $('favorites').innerHTML=rows.length?rows.map(t=>`<div class="track" data-id="${t.id}">
    <div class="track-main"><div class="track-title"><span class="badge provider-badge">${esc(providerName(t.provider||t.kind))}</span><strong>${esc(t.title)}</strong></div>
    <div class="track-meta"><span>${esc(t.artist||'Unknown artist')}</span><span>${sec(t.duration)}</span>${t.source_url&&String(t.source_url).startsWith('http')?`<a class="catalog-link" href="${esc(t.source_url)}" target="_blank" rel="noopener noreferrer">source ↗</a>`:''}</div></div>
    ${trackActions([`<button onclick="favoriteAdd(${t.id},'queue')">Q</button>`,`<button class="ghost" onclick="favoriteAdd(${t.id},'playlist')">P</button>`,`<button class="ghost danger-lite" title="Remove from favourites" onclick="removeFavorite(${t.id})">✕</button>`])}
  </div>`).join(''):'<div class="empty">No saved tracks yet. Hit ★ on a song, Suno link or remote result.</div>';
  $('favoritesSummary').textContent=`${rows.length} saved track${rows.length===1?'':'s'}`;
}
window.favoriteAdd=async(id,target)=>{try{await api(`/api/favorites/${id}/${target}`,{method:'POST'});await loadLibrary();await refreshAll();}catch(e){alert(e.message)}};
window.removeFavorite=async id=>{try{await api(`/api/favorites/${id}`,{method:'DELETE'});await loadFavorites();}catch(e){alert(e.message)}};
let favoritesSearchTimer=null;
$('favoritesSearch').addEventListener('input',()=>{clearTimeout(favoritesSearchTimer);favoritesSearchTimer=setTimeout(loadFavorites,180);});
$('clearFavoritesSearch').onclick=()=>{$('favoritesSearch').value='';loadFavorites();$('favoritesSearch').focus();};

// Queue / playlist -------------------------------------------------------------
function renderQueue() {
  $('queue').innerHTML=state.queue.length?state.queue.map((t,i)=>{
    const locked=t.status==='reserved';
    return `<div class="track reorderable ${locked?'locked':''}" data-id="${t.id}">
      ${locked?'<span class="drag-handle disabled" title="Already preloaded">🔒</span>':'<span class="drag-handle" title="Drag to reorder" aria-label="Drag to reorder">⋮⋮</span>'}
      <div class="track-main"><div class="track-title"><input class="position-input" type="number" min="1" max="${state.queue.length}" value="${i+1}" ${locked?'disabled title="ON DECK position is locked"':`title="Type a queue position and press Enter" onchange="moveToPosition('queue',${t.id},this.value,this)" onkeydown="positionKey(event,'queue',${t.id},this)"`}>${locked?'<span class="badge deck">ON DECK</span>':''}${isCatalogProvider(t.source_type)?`<span class="badge provider-badge compact-provider" title="${esc(providerName(t.source_type))}">${esc(providerShort(t.source_type))}</span>`:''}<strong>${esc(t.title)}</strong></div>
      <div class="track-meta"><span>${esc(t.artist||'Unknown artist')}</span><span>${sec(t.duration)}</span><span>in ${sec(t.offset_seconds)}</span><span class="eta">${t.expected_start_epoch?clock(t.expected_start_epoch):'ETA paused'}</span>${t.source_url&&String(t.source_url).startsWith('http')?`<a class="catalog-link" href="${esc(t.source_url)}" target="_blank" rel="noopener noreferrer">source ↗</a>`:''}</div></div>
      ${trackActions([t.previewable?`<button class="ghost preview-btn" title="Play this cached/local song in the browser only" onclick="previewQueue(${t.id},'${enc(t.title)}','${enc(t.artist||'Unknown artist')}')">▶</button>`:'',`<button class="ghost star-btn" title="Save this song to favourites" onclick="favoriteQueue(${t.id})">★</button>`,locked?'':`<button class="ghost" onclick="removeQueue(${t.id})">✕</button>`].filter(Boolean))}
    </div>`;
  }).join(''):'<div class="empty">Queue empty. Add tracks or whole folders from the library.</div>';
  enableDnD($('queue'),'/api/queue/reorder');
}
function renderPlaylist() {
  $('playlist').innerHTML=state.playlist.length?state.playlist.map((t,i)=>`<div class="track reorderable" data-id="${t.id}">
    <span class="drag-handle" title="Drag to reorder" aria-label="Drag to reorder">⋮⋮</span>
    <div class="track-main"><div class="track-title"><input class="position-input" type="number" min="1" max="${state.playlist.length}" value="${i+1}" title="Type a playlist position and press Enter" onchange="moveToPosition('playlist',${t.id},this.value,this)" onkeydown="positionKey(event,'playlist',${t.id},this)">${isCatalogProvider(t.source_type)?`<span class="badge provider-badge compact-provider" title="${esc(providerName(t.source_type))}">${esc(providerShort(t.source_type))}</span>`:''}<strong>${esc(t.title)}</strong></div>
    <div class="track-meta"><span>${esc(t.artist||'Unknown artist')}</span><span>${sec(t.duration)}</span><span>+${sec(t.offset_seconds)}</span><span class="eta">${clock(t.if_started_now_epoch)}</span>${t.source_url&&String(t.source_url).startsWith('http')?`<a class="catalog-link" href="${esc(t.source_url)}" target="_blank" rel="noopener noreferrer">source ↗</a>`:''}</div></div>
    ${trackActions([t.previewable?`<button class="ghost preview-btn" title="Play this cached/local song in the browser only" onclick="previewPlaylist(${t.id},'${enc(t.title)}','${enc(t.artist||'Unknown artist')}')">▶</button>`:'',`<button onclick="queuePlaylistItem(${t.id})" title="Send this song to Queue">Q</button>`,`<button class="ghost star-btn" title="Save this song to favourites" onclick="favoritePlaylist(${t.id})">★</button>`,`<button class="ghost" onclick="removePlaylist(${t.id})">✕</button>`].filter(Boolean))}
  </div>`).join(''):'<div class="empty">Playlist empty.</div>';
  enableDnD($('playlist'),'/api/playlist/reorder');
}
window.positionKey=(event,kind,id,input)=>{
  // Submit directly on Enter. Relying on blur/change was fragile because the
  // periodic status refresh can replace list DOM nodes.
  if(event.key==='Enter'){
    event.preventDefault();
    event.stopPropagation();
    moveToPosition(kind,id,input.value,input);
  }
  else if(event.key==='Escape'){
    event.preventDefault();
    event.stopPropagation();
    const rows=kind==='queue'?state.queue:state.playlist;
    const idx=rows.findIndex(r=>Number(r.id)===Number(id));
    if(idx>=0)input.value=idx+1;
    input.blur();
  }
};
window.moveToPosition=async(kind,id,value,input)=>{
  const rows=kind==='queue'?state.queue:state.playlist;
  const current=rows.findIndex(r=>Number(r.id)===Number(id))+1;
  let position=Math.trunc(Number(value));
  if(!Number.isFinite(position)||position<1||position>rows.length){input.value=current||1;return;}
  if(position===current)return;
  input.disabled=true;
  try{
    await api(`/api/${kind}/${id}/position`,jsonOpts('POST',{position}));
    await refreshAll();
  }catch(err){
    input.disabled=false;input.value=current||1;alert(err.message);
  }
};

function dndIds(container) {
  return [...container.querySelectorAll('.track[data-id]')].map(x=>Number(x.dataset.id));
}
function sameOrder(a,b) {
  return a.length===b.length && a.every((v,i)=>v===b[i]);
}
function clearDnDVisuals(container) {
  container.querySelectorAll('.dragging,.dragover').forEach(x=>x.classList.remove('dragging','dragover'));
}
async function finishDnD() {
  const drag=state.drag;
  if(!drag || drag.saving) return;
  drag.saving=true;
  const ids=dndIds(drag.container);
  clearDnDVisuals(drag.container);
  try {
    if(!sameOrder(ids,drag.initialIds)) {
      await api(drag.endpoint,jsonOpts('POST',{ordered_ids:ids}));
    }
  } catch(err) {
    alert(err.message);
  } finally {
    state.drag=null;
    await refreshAll();
  }
}
function enableDnD(container,endpoint) {
  container.querySelectorAll('.track.reorderable:not(.locked)').forEach(row=>{
    const handle=row.querySelector('.drag-handle');
    if(!handle) return;

    handle.addEventListener('pointerdown',e=>{
      if(e.button!==undefined && e.button!==0) return;
      e.preventDefault();
      if(state.drag) return;
      state.drag={container,endpoint,row,id:Number(row.dataset.id),initialIds:dndIds(container),pointerId:e.pointerId,saving:false};
      row.classList.add('dragging');
      handle.classList.add('active');
      try { handle.setPointerCapture(e.pointerId); } catch {}
    });

    handle.addEventListener('pointermove',e=>{
      const drag=state.drag;
      if(!drag || drag.row!==row || drag.saving) return;
      e.preventDefault();
      const hit=document.elementFromPoint(e.clientX,e.clientY);
      const target=hit?.closest?.('.track[data-id]');
      container.querySelectorAll('.dragover').forEach(x=>x.classList.remove('dragover'));
      if(!target || target.parentElement!==container || target===row || target.classList.contains('locked')) return;
      const rect=target.getBoundingClientRect();
      const after=e.clientY > rect.top + rect.height/2;
      container.insertBefore(row, after ? target.nextSibling : target);
      target.classList.add('dragover');
    });

    const endPointer=async e=>{
      const drag=state.drag;
      if(!drag || drag.row!==row || drag.saving) return;
      e.preventDefault();
      handle.classList.remove('active');
      try { if(handle.hasPointerCapture(e.pointerId)) handle.releasePointerCapture(e.pointerId); } catch {}
      await finishDnD();
    };
    handle.addEventListener('pointerup',endPointer);
    handle.addEventListener('pointercancel',endPointer);

    // Desktop fallback for browsers/extensions that interfere with Pointer Events.
    // Keep native dragging on the handle only so editing the position input never
    // accidentally grabs the whole row.
    handle.draggable=true;
    handle.addEventListener('dragstart',e=>{
      if(state.drag) return;
      state.drag={container,endpoint,row,id:Number(row.dataset.id),initialIds:dndIds(container),pointerId:null,saving:false};
      row.classList.add('dragging');
      e.dataTransfer.effectAllowed='move';
      e.dataTransfer.setData('text/plain',String(row.dataset.id));
    });
    handle.addEventListener('dragend',()=>{ if(state.drag?.row===row && !state.drag.saving) finishDnD(); });
  });

  container.addEventListener('dragover',e=>{
    const drag=state.drag;
    if(!drag || drag.container!==container || drag.saving) return;
    e.preventDefault();
    e.dataTransfer.dropEffect='move';
    const target=e.target.closest?.('.track[data-id]');
    if(!target || target===drag.row || target.classList.contains('locked')) return;
    const rect=target.getBoundingClientRect();
    const after=e.clientY > rect.top + rect.height/2;
    container.insertBefore(drag.row,after?target.nextSibling:target);
  });
  container.addEventListener('drop',e=>{
    if(!state.drag || state.drag.container!==container) return;
    e.preventDefault();
    finishDnD();
  });
}
window.removeQueue=async id=>{try{await api(`/api/queue/${id}`,{method:'DELETE'});refreshAll();}catch(e){alert(e.message)}};
window.removePlaylist=async id=>{await api(`/api/playlist/${id}`,{method:'DELETE'});refreshAll();};
window.queuePlaylistItem=async id=>{await api(`/api/playlist/${id}/queue`,{method:'POST'});refreshAll();};
window.favoriteQueue=async id=>{try{await api(`/api/queue/${id}/favorite`,{method:'POST'});await loadFavorites();}catch(e){alert(e.message)}};
window.favoritePlaylist=async id=>{try{await api(`/api/playlist/${id}/favorite`,{method:'POST'});await loadFavorites();}catch(e){alert(e.message)}};
$('clearQueue').onclick=async()=>{await api('/api/queue/clear',{method:'POST'});refreshAll();};
$('clearPlaylist').onclick=async()=>{await api('/api/playlist/clear',{method:'POST'});refreshAll();};
$('queuePlaylist').onclick=async()=>{const r=await api('/api/queue/all-playlist',{method:'POST'});refreshAll();alert(`Queued ${r.queued} playlist tracks.${r.failed?.length?` ${r.failed.length} failed to resolve.`:''}`);};

// TXT playlist / set import ----------------------------------------------------
$('importTxtOpen').onclick=()=>{
  $('txtImportFile').value='';$('txtImportText').value='';$('txtImportTarget').value='playlist';$('txtImportSource').value='auto';$('txtSkipDuplicates').checked=true;$('txtContinueErrors').checked=true;
  $('txtImportMsg').className='msg';$('txtImportMsg').textContent='';
  $('txtImportReport').classList.add('hidden');$('txtImportReport').innerHTML='';
  $('txtImportDialog').showModal();
};
function renderTxtImportReport(result){
  const box=$('txtImportReport'), rows=result.report||[], sum=result.summary||{};
  box.classList.remove('hidden');
  box.innerHTML=`<div class="import-report-row"><span class="line">—</span><span class="status">SUMMARY</span><div><strong>${Number(sum.added)||0} added · ${Number(sum.skipped)||0} skipped · ${Number(sum.failed)||0} failed</strong><small>${Number(sum.lines)||0} song lines processed → ${esc(result.target||'playlist')} · ${esc(providerName(result.source||'auto'))}</small></div></div>`+
    rows.map(r=>`<div class="import-report-row ${esc(r.status||'')}"><span class="line">${Number(r.line)||''}</span><span class="status">${esc(r.status||'')}</span><div><strong>${esc(r.title||r.input||'')}</strong><small>${r.artist?`${esc(r.artist)} · `:''}${r.provider?`${esc(providerName(r.provider))} · `:''}${esc(r.detail||r.input||'')}</small></div></div>`).join('');
}
$('txtImportStart').onclick=async()=>{
  const file=$('txtImportFile').files[0], pasted=$('txtImportText').value.trim(), msg=$('txtImportMsg'), button=$('txtImportStart');
  if(!file&&!pasted){msg.className='msg bad';msg.textContent='Choose a .txt file or paste a song list.';return;}
  const fd=new FormData();
  if(file) fd.append('file',file); else fd.append('text',pasted);
  fd.append('target',$('txtImportTarget').value);
  fd.append('source',$('txtImportSource').value);
  fd.append('skip_duplicates',$('txtSkipDuplicates').checked?'true':'false');
  fd.append('continue_on_error',$('txtContinueErrors').checked?'true':'false');
  button.disabled=true;msg.className='msg';
  msg.textContent=`Finding songs in ${$('txtImportSource').selectedOptions[0]?.textContent||'selected sources'}…`;
  try{
    const r=await api('/api/import/txt',{method:'POST',body:fd});
    renderTxtImportReport(r);
    const sum=r.summary||{};
    msg.className=`msg ${sum.failed?'bad':'good'}`;
    msg.textContent=`Import finished: ${sum.added||0} added, ${sum.skipped||0} skipped, ${sum.failed||0} failed.`;
    await Promise.all([refreshAll(),loadFavorites(),loadLibrary()]);
  }catch(e){msg.className='msg bad';msg.textContent=e.message;}
  finally{button.disabled=false;}
};

async function refreshAll() {
  try {
    const [status,queue,playlist]=await Promise.all([api('/api/status'),api('/api/queue'),api('/api/playlist')]);
    state.status=status;state.nowReceived=Date.now();renderStatus();

    // Keep fresh data in memory, but never rebuild a list while its position field
    // is focused. Otherwise the 2-second status poll destroys the input node while
    // the user is typing. Dragging gets the same protection.
    const active=document.activeElement;
    const editingQueue=!!(active?.classList?.contains('position-input') && active.closest('#queue'));
    const editingPlaylist=!!(active?.classList?.contains('position-input') && active.closest('#playlist'));

    state.queue=queue;
    state.playlist=playlist;
    if(state.drag?.container!==$('queue') && !editingQueue) renderQueue();
    if(state.drag?.container!==$('playlist') && !editingPlaylist) renderPlaylist();
  } catch(e) { /* auth handler shows login */ }
}
function renderStatus() {
  const s=state.status;if(!s)return;const np=s.now_playing,sh=s.shoutcast||{};
  $('shoutcastStatus').textContent=sh.engine_running?(sh.output_active===false?'Disconnected':sh.output_active===true?'Connected':'Engine ready'):'Engine stopped';
  $('shoutcastDot').className='dot '+(sh.output_active===true?'good':sh.engine_running?'warn':'bad');
  $('airDot').className='dot '+(np&&s.playout_state==='playing'&&sh.output_active===true?'good':'unknown');
  $('nowTitle').textContent=np?.title||'Nothing on air'; $('nowArtist').textContent=np?.artist||'—';
  const ns=$('nowSource');if(np&&isCatalogProvider(np.source_type)){ns.classList.remove('hidden');ns.innerHTML=`<span class="badge provider-badge">${esc(providerName(np.source_type))}</span>${np.source_url&&String(np.source_url).startsWith('http')?` <a class="catalog-link" href="${esc(np.source_url)}" target="_blank" rel="noopener noreferrer">open source ↗</a>`:''}`;}else{ns.classList.add('hidden');ns.textContent='';}
  $('nowCompact').textContent=np?`${np.artist?np.artist+' — ':''}${np.title}`:'Nothing on air';
  const pe=$('playbackError');if(s.playback_error){pe.textContent=s.playback_error;pe.classList.remove('hidden')}else{pe.textContent='';pe.classList.add('hidden')}
  $('queueSetTime').textContent=sec(s.queue_summary?.set_remaining_seconds);$('queueEnds').textContent=clock(s.queue_summary?.set_end_epoch);$('playlistSetTime').textContent=sec(s.playlist_summary?.playlist_seconds);renderProgress();
  if(sh.error){$('shoutcastStatus').textContent='Engine error';$('shoutcastDot').className='dot bad';}
}
function renderProgress() {
  const s=state.status,np=s?.now_playing;
  if(!np){['progressElapsed','progressDuration','miniTime'].forEach(id=>$(id).textContent='0:00');$('progressRemaining').textContent='-0:00';$('progressFill').style.width='0%';$('miniProgress').style.width='0%';return;}
  let elapsed=Number(np.elapsed||0);const active=s.playout_state==='playing'&&s.shoutcast?.output_active===true;
  if(active)elapsed+=(Date.now()-state.nowReceived)/1000;
  const dur=Number(np.duration||0);if(dur)elapsed=Math.min(dur,elapsed);const pct=dur?Math.max(0,Math.min(100,elapsed/dur*100)):0;
  $('progressElapsed').textContent=sec(elapsed);$('progressDuration').textContent=sec(dur);$('progressRemaining').textContent='-'+sec(Math.max(0,dur-elapsed));$('progressFill').style.width=`${pct}%`;$('miniProgress').style.width=`${pct}%`;$('miniTime').textContent=`${sec(elapsed)} / ${sec(dur)}`;
}
async function control(action){try{await api(`/api/control/${action}`,{method:'POST'});await refreshAll()}catch(e){alert(e.message)}}
$('connect').onclick=()=>control('connect');$('disconnect').onclick=()=>control('disconnect');$('play').onclick=()=>control('play');$('pause').onclick=()=>control('pause');$('stop').onclick=()=>control('stop');
$('skip').onclick=async()=>{try{await api('/api/skip',{method:'POST'});setTimeout(refreshAll,500)}catch(e){alert(e.message)}};

// Suno -------------------------------------------------------------------------
async function sendSuno(target) {
  const url=$('sunoUrl').value.trim(),msg=$('sunoMsg');
  if(!url){msg.className='msg bad';msg.textContent='Paste a Suno link or UUID first.';return;}
  msg.className='msg';msg.textContent='Resolving Suno UUID / caching audio…';
  try{
    const r=await api(target,jsonOpts('POST',{url,title:$('sunoTitle').value.trim()||null,artist:$('sunoArtist').value.trim()||null}));
    msg.className='msg good';msg.textContent='Audio resolved through provider and cached.';
    $('sunoUrl').value='';await loadLibrary();await refreshAll();
  } catch(e){msg.className='msg bad';msg.textContent=e.message;}
}
$('sunoQueue').onclick=()=>sendSuno('/api/queue/url');$('sunoPlaylist').onclick=()=>sendSuno('/api/playlist/url');
$('sunoFavorite').onclick=async()=>{
  const url=$('sunoUrl').value.trim(),msg=$('sunoMsg');
  if(!url){msg.className='msg bad';msg.textContent='Paste a Suno link, UUID or direct audio URL first.';return;}
  msg.className='msg';msg.textContent='Saving to favourites…';
  try{const r=await api('/api/favorites/url',jsonOpts('POST',{url,title:$('sunoTitle').value.trim()||null,artist:$('sunoArtist').value.trim()||null}));msg.className='msg good';msg.textContent=r.resolved_url?.startsWith('suno:')?'★ Suno track saved — no need to paste it again.':'★ Direct audio saved.';await loadFavorites();}catch(e){msg.className='msg bad';msg.textContent=e.message;}
};

// Local / remote source tabs ---------------------------------------------------
[...document.querySelectorAll('.tab')].forEach(btn=>btn.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));btn.classList.add('active');
  const tab=btn.dataset.tab;
  $('localTab').classList.toggle('hidden',tab!=='library');
  $('favoritesTab').classList.toggle('hidden',tab!=='favorites');
  $('sourcesTab').classList.toggle('hidden',tab!=='sources');
  $('catalogsTab').classList.toggle('hidden',tab!=='catalogs');
  if(tab==='favorites')loadFavorites();
  if(tab==='sources')loadSources();
});
function selectedSource(){return state.sources.find(s=>Number(s.id)===Number(state.sourceId))||null;}
function jellyfinSegmentName(seg){
  const i=String(seg||'').indexOf('~');if(i<0)return String(seg||'').slice(0,8);
  try{return decodeURIComponent(String(seg).slice(i+1));}catch{return String(seg).slice(i+1);}
}
function sourcePathLabel(path){
  const src=selectedSource();if(!path)return '/';
  if(src?.kind!=='jellyfin')return '/'+path;
  return '/'+String(path).split('/').filter(Boolean).map(jellyfinSegmentName).join('/');
}
async function loadSources(){
  state.sources=await api('/api/sources');const sel=$('sourceSelect');const old=String(state.sourceId||'');
  sel.innerHTML='<option value="">Choose remote source…</option>'+state.sources.map(s=>`<option value="${s.id}">${esc(s.name)} (${esc(s.kind)})</option>`).join('');
  if(state.sources.some(s=>String(s.id)===old))sel.value=old;
  if(sel.value)browseRemote();else $('sourceBrowser').innerHTML='<div class="empty">Add a WebDAV, FTP/FTPS or Jellyfin source.</div>';
}
$('sourceSelect').onchange=()=>{state.sourceId=Number($('sourceSelect').value)||null;state.sourcePath='';state.sourceSearch='';$('sourceSearch').value='';browseRemote();};
async function browseRemote(){
  if(!state.sourceId){$('sourceBrowser').innerHTML='<div class="empty">Choose a source.</div>';return;}
  const q=$('sourceSearch').value.trim();state.sourceSearch=q;
  $('sourcePath').textContent=q?`Search: “${q}”`:sourcePathLabel(state.sourcePath);$('sourceUp').disabled=!state.sourcePath||!!q;$('sourceBrowser').innerHTML='<div class="empty">Loading…</div>';
  try{
    const qs=new URLSearchParams({path:state.sourcePath});if(q)qs.set('q',q);
    const rows=await api(`/api/sources/${state.sourceId}/browse?${qs}`);state.sourceRows=rows;
    $('sourceBrowser').innerHTML=rows.length?rows.map(x=>{
      if(x.is_dir)return `<div class="track folder-row" onclick="openRemoteFolder('${enc(x.path)}')"><div class="track-main"><div class="track-title"><strong>📁 ${esc(x.name)}</strong>${x.kind?`<span class="badge">${esc(x.kind)}</span>`:''}</div></div></div>`;
      const meta=[x.artist, x.duration?sec(x.duration):''].filter(Boolean).map(v=>`<span>${esc(v)}</span>`).join('');
      return `<div class="track"><div class="track-main"><div class="track-title"><strong>${esc(x.name)}</strong>${x.kind?`<span class="badge">${esc(x.kind)}</span>`:''}</div><div class="track-meta">${meta||`<span>${esc(selectedSource()?.kind==='jellyfin'?'Jellyfin audio':x.path)}</span>`}</div></div>${trackActions([`<button onclick="remoteAdd('queue','${enc(x.path)}')">Q</button>`,`<button class="ghost" onclick="remoteAdd('playlist','${enc(x.path)}')">P</button>`,`<button class="ghost star-btn" title="Save to favourites" onclick="remoteFavorite('${enc(x.path)}')">★</button>`])}</div>`;
    }).join(''):'<div class="empty">No matching music here.</div>';
  }catch(e){$('sourceBrowser').innerHTML=`<div class="empty">${esc(e.message)}</div>`;}
}
window.openRemoteFolder=p=>{state.sourcePath=decodeURIComponent(p);state.sourceSearch='';$('sourceSearch').value='';browseRemote();};
$('sourceUp').onclick=()=>{state.sourcePath=state.sourcePath.split('/').slice(0,-1).join('/');browseRemote();};
let sourceSearchTimer=null;$('sourceSearch').addEventListener('input',()=>{clearTimeout(sourceSearchTimer);sourceSearchTimer=setTimeout(browseRemote,250);});
$('clearSourceSearch').onclick=()=>{$('sourceSearch').value='';state.sourceSearch='';browseRemote();};
window.remoteAdd=async(target,p)=>{try{await api(`/api/sources/item/${target}`,jsonOpts('POST',{source_id:state.sourceId,path:decodeURIComponent(p)}));await loadLibrary();await refreshAll();}catch(e){alert(e.message)}};
window.remoteFavorite=async p=>{try{const path=decodeURIComponent(p),x=(state.sourceRows||[]).find(r=>!r.is_dir&&String(r.path)===path);await api('/api/favorites/source',jsonOpts('POST',{source_id:state.sourceId,path,title:x?.name||'',artist:x?.artist||'',duration:x?.duration||null}));await loadFavorites();}catch(e){alert(e.message)}};
$('sourceAddOpen').onclick=()=>{$('sourceMsg').textContent='';updateSourceKindUI();$('sourceDialog').showModal();};
function updateSourceKindUI(){
  const kind=$('srcKind').value,root=$('srcRoot'),url=$('srcUrl'),help=$('sourceHelp');
  if(kind==='jellyfin'){
    url.placeholder='https://jellyfin.example.com or http://192.168.1.20:8096';root.disabled=true;root.placeholder='Not used by Jellyfin';
    help.textContent='Jellyfin: enter the normal Jellyfin username and password. Tasia logs in as that user, so Jellyfin library permissions apply.';
  }else if(kind==='ftp'){
    url.placeholder='ftp://host/music/ or ftps://host/music/';root.disabled=false;root.placeholder='Root folder (optional)';help.textContent='FTP/FTPS uses the username and password below.';
  }else{
    url.placeholder='https://dav.example.com/music/';root.disabled=false;root.placeholder='Root folder (optional)';help.textContent='WebDAV supports Basic and Digest username/password authentication.';
  }
}
$('srcKind').onchange=updateSourceKindUI;
function sourceFormBody(){return {name:$('srcName').value.trim()||'Remote music',kind:$('srcKind').value,url:$('srcUrl').value.trim(),root_path:$('srcRoot').disabled?'':$('srcRoot').value.trim(),username:$('srcUser').value.trim(),password:$('srcPass').value};}
$('testSource').onclick=async e=>{e.preventDefault();const msg=$('sourceMsg');msg.className='msg';msg.textContent='Testing connection…';try{const r=await api('/api/sources/test',jsonOpts('POST',sourceFormBody()));msg.className='msg good';msg.textContent=r.message||'Connection works.';}catch(err){msg.className='msg bad';msg.textContent=err.message;}};
$('saveSource').onclick=async e=>{e.preventDefault();const msg=$('sourceMsg');msg.className='msg';msg.textContent='Testing credentials…';try{const body=sourceFormBody();await api('/api/sources/test',jsonOpts('POST',body));await api('/api/sources',jsonOpts('POST',body));msg.className='msg good';msg.textContent='Source connected and saved.';$('sourceDialog').close();await loadSources();}catch(err){msg.className='msg bad';msg.textContent=err.message;}};

// Online streaming catalogs ---------------------------------------------------
function providerName(p){return ({auto:'Auto / All Sources + Local',all:'All Sources',universal:'Universal Search',soundcloud:'SoundCloud Search',audius:'Audius',jamendo:'Jamendo',stremio:'Stremio Addon','btch-spotify':'Spotify','btch-soundcloud':'SoundCloud','btch-gdrive':'Google Drive',suno:'Suno',direct:'Direct audio',jellyfin:'Jellyfin',webdav:'WebDAV',ftp:'FTP / FTPS',local:'Local library'})[p]||p;}
function providerShort(p){return ({universal:'UNIV',soundcloud:'SC',audius:'AUDIUS',jamendo:'JAM',stremio:'STREMIO','btch-spotify':'SPOT','btch-soundcloud':'SC-BTCH','btch-gdrive':'GDRIVE',suno:'SUNO',direct:'URL',jellyfin:'JELLY',webdav:'DAV',ftp:'FTP',local:'LOCAL'})[String(p||'').toLowerCase()]||String(p||'').slice(0,8).toUpperCase();}
function isCatalogProvider(p){return ['universal','soundcloud','audius','jamendo','stremio','btch-spotify','btch-soundcloud','btch-gdrive'].includes(String(p||'').toLowerCase());}
function renderCatalogResults(){
  const rows=state.catalogResults||[];
  $('catalogResults').innerHTML=rows.length?rows.map(t=>{
    const id=enc(t.id), provider=esc(t.provider), source=t.url?`<a class="catalog-link" href="${esc(t.url)}" target="_blank" rel="noopener noreferrer">source ↗</a>`:'', license=t.license&&String(t.license).startsWith('http')?`<a class="catalog-license" href="${esc(t.license)}" target="_blank" rel="noopener noreferrer">license ↗</a>`:(t.license?`<span class="catalog-license">${esc(t.license)}</span>`:'');
    return `<div class="track"><div class="track-main"><div class="track-title"><span class="badge provider-badge">${esc(providerName(t.provider))}</span><strong>${esc(t.title)}</strong></div><div class="track-meta"><span>${esc(t.artist||'Unknown artist')}</span><span>${sec(t.duration)}</span>${source}${license}</div></div>${trackActions([`<button onclick="catalogAdd('queue','${provider}','${id}')">Q</button>`,`<button class="ghost" onclick="catalogAdd('playlist','${provider}','${id}')">P</button>`,`<button class="ghost star-btn" title="Save to favourites" onclick="catalogFavorite('${provider}','${id}')">★</button>`])}</div>`;
  }).join(''):'<div class="empty">No playable results.</div>';
}
async function searchCatalog(){
  const provider=$('catalogProvider').value,q=$('catalogSearch').value.trim(),msg=$('catalogStatus');
  if(!q&&provider!=='stremio'){msg.textContent='Type something to search.';$('catalogSearch').focus();return;}
  msg.textContent=q?`Searching ${providerName(provider)}…`:`Browsing ${providerName(provider)}…`;$('catalogResults').innerHTML='<div class="empty">Loading…</div>';
  try{state.catalogResults=await api(`/api/catalog/${provider}/search?q=${encodeURIComponent(q)}&limit=40`);renderCatalogResults();msg.textContent=`${state.catalogResults.length} playable result${state.catalogResults.length===1?'':'s'} from ${providerName(provider)}.`;}catch(e){state.catalogResults=[];$('catalogResults').innerHTML=`<div class="empty">${esc(e.message)}</div>`;msg.textContent='Search failed.';}
}
$('catalogSearchBtn').onclick=searchCatalog;$('catalogSearch').addEventListener('keydown',e=>{if(e.key==='Enter')searchCatalog();});
$('catalogProvider').onchange=()=>{const p=$('catalogProvider').value;state.catalogResults=[];$('catalogResults').innerHTML='<div class="empty">Search this provider.</div>';$('catalogStatus').textContent=`${providerName(p)} selected.`;$('catalogTestBtn').disabled=p==='all';$('catalogSearch').placeholder=p==='all'?'Search Spotify + Universal + SoundCloud + Audius + Jamendo + Stremio…':(p==='btch-spotify'?'Search Spotify by song / artist, or paste a Spotify URL…':(p==='universal'?'Song, artist, YouTube URL or Spotify URL…':(p==='stremio'?'Search addon, or leave blank to browse…':(p==='btch-soundcloud'?'Paste SoundCloud track URL…':(p==='btch-gdrive'?'Paste public Google Drive file URL…':'Search this catalog…')))));};
$('catalogTestBtn').onclick=async()=>{const p=$('catalogProvider').value,m=$('catalogStatus');if(p==='all'){m.textContent='All Sources searches every configured provider; test providers individually.';return;}m.textContent=`Testing ${providerName(p)}…`;try{const r=await api(`/api/catalog/${p}/test`,{method:'POST'});m.textContent=r.message||'Source works.';}catch(e){m.textContent=e.message;}};
window.catalogAdd=async(target,provider,id)=>{try{await api('/api/catalog/item/'+target,jsonOpts('POST',{provider,track_id:decodeURIComponent(id)}));await refreshAll();}catch(e){alert(e.message)}};
window.catalogFavorite=async(provider,id)=>{try{const trackId=decodeURIComponent(id),t=(state.catalogResults||[]).find(x=>String(x.provider)===provider&&String(x.id)===trackId);if(!t)throw new Error('Search result is no longer available');await api('/api/favorites/catalog',jsonOpts('POST',{provider,track_id:trackId,title:t.title||'Untitled',artist:t.artist||'',duration:t.duration||null,source_url:t.url||'',artwork:t.artwork||''}));await loadFavorites();}catch(e){alert(e.message)}};

async function loadCatalogSettings(){
  const rows=await api('/api/catalog/settings');state.catalogSettings=Object.fromEntries(rows.map(x=>[x.provider,x]));
  const un=state.catalogSettings.universal||{},sc=state.catalogSettings.soundcloud||{},au=state.catalogSettings.audius||{},ja=state.catalogSettings.jamendo||{},st=state.catalogSettings.stremio||{};
  $('universalConverterUrl').value=un.base_url||'https://yapi.is-on.click/api/convert';
  $('scClientId').value=sc.client_id||'';$('scClientSecret').value='';$('scClientSecret').placeholder=sc.client_secret_set?'Saved — leave blank to keep':'Client Secret';$('scClearSecret').checked=false;
  $('audiusBearer').value='';$('audiusBearer').placeholder=au.bearer_token_set?'Saved — leave blank to keep':'Bearer Token (optional)';$('audiusClearBearer').checked=false;
  $('jamendoClientId').value=ja.client_id||'';$('stremioManifestUrl').value=st.base_url||'';
  return rows;
}
async function saveCatalog(provider,body,msgId){const msg=$(msgId);msg.className='msg';msg.textContent='Saving…';try{const r=await api(`/api/catalog/settings/${provider}`,jsonOpts('PUT',body));msg.className='msg good';msg.textContent=`${providerName(provider)} settings saved.`;await loadCatalogSettings();return r;}catch(e){msg.className='msg bad';msg.textContent=e.message;throw e;}}
function catalogForm(provider){const empty={base_url:'',client_id:'',client_secret:'',api_key:'',bearer_token:'',clear_client_secret:false,clear_bearer_token:false};if(provider==='universal')return {...empty,base_url:$('universalConverterUrl').value.trim()||'https://yapi.is-on.click/api/convert'};if(provider==='soundcloud')return {...empty,client_id:$('scClientId').value.trim(),client_secret:$('scClientSecret').value,clear_client_secret:$('scClearSecret').checked};if(provider==='audius')return {...empty,bearer_token:$('audiusBearer').value,clear_bearer_token:$('audiusClearBearer').checked};if(provider==='jamendo')return {...empty,client_id:$('jamendoClientId').value.trim()};if(provider==='stremio')return {...empty,base_url:$('stremioManifestUrl').value.trim()};return empty;}
async function testCatalog(provider,msgId){const msg=$(msgId);msg.className='msg';msg.textContent='Testing…';try{const r=await api(`/api/catalog/${provider}/test`,jsonOpts('POST',catalogForm(provider)));msg.className='msg good';msg.textContent=r.message||'Connection works.';}catch(e){msg.className='msg bad';msg.textContent=e.message;}}
$('saveUniversal').onclick=async e=>{e.preventDefault();try{await saveCatalog('universal',catalogForm('universal'),'universalCatalogMsg');await loadUniversalStatus();}catch{}};
$('saveSoundCloud').onclick=async e=>{e.preventDefault();try{await saveCatalog('soundcloud',catalogForm('soundcloud'),'scCatalogMsg');}catch{}};
$('testSoundCloud').onclick=e=>{e.preventDefault();testCatalog('soundcloud','scCatalogMsg');};
$('saveAudius').onclick=async e=>{e.preventDefault();try{await saveCatalog('audius',catalogForm('audius'),'audiusCatalogMsg');}catch{}};
$('testAudius').onclick=e=>{e.preventDefault();testCatalog('audius','audiusCatalogMsg');};
$('saveJamendo').onclick=async e=>{e.preventDefault();try{await saveCatalog('jamendo',catalogForm('jamendo'),'jamendoCatalogMsg');}catch{}};
$('testJamendo').onclick=e=>{e.preventDefault();testCatalog('jamendo','jamendoCatalogMsg');};
$('saveStremio').onclick=async e=>{e.preventDefault();try{await saveCatalog('stremio',catalogForm('stremio'),'stremioCatalogMsg');}catch{}};
$('testStremio').onclick=e=>{e.preventDefault();testCatalog('stremio','stremioCatalogMsg');};

// Universal Search -----------------------------------------------------------
async function loadUniversalStatus(){
  const msg=$('universalCatalogMsg');
  try{const r=await api('/api/universal/status');const un=(state.catalogSettings||{}).universal||{};const endpoint=un.base_url||$('universalConverterUrl').value||'https://yapi.is-on.click/api/convert';msg.className='msg good';msg.textContent=(r.message||'Universal search ready.')+` Playback API: ${endpoint}.`+(r.cookies_set?' Search cookies installed.':' No search cookies installed.');return r;}catch(e){msg.className='msg bad';msg.textContent=e.message;return null;}
}
$('testUniversal').onclick=e=>{e.preventDefault();loadUniversalStatus();};
$('universalCookies').onchange=async()=>{const f=$('universalCookies').files[0],msg=$('universalCatalogMsg');if(!f)return;const form=new FormData();form.append('file',f);msg.className='msg';msg.textContent='Uploading cookies.txt…';try{await api('/api/universal/cookies',{method:'POST',body:form});$('universalCookies').value='';await loadUniversalStatus();}catch(e){msg.className='msg bad';msg.textContent=e.message;}};
$('clearUniversalCookies').onclick=async e=>{e.preventDefault();const msg=$('universalCatalogMsg');try{await api('/api/universal/cookies',{method:'DELETE'});await loadUniversalStatus();}catch(err){msg.className='msg bad';msg.textContent=err.message;}};

// Suno authenticated API session ------------------------------------------------
async function loadSunoAuthStatus(){
  const msg=$('sunoAuthMsg');
  try{
    const r=await api('/api/suno/auth/status');
    $('sunoCookieHeader').value='';
    $('sunoClientCookie').value='';
    $('sunoConnectorKey').value=r.connector_key||'';
    const age=r.updated_at_epoch?Math.max(0,Math.round(Date.now()/1000-r.updated_at_epoch)):null;
    const ageText=age===null?'':(age<120?' · refreshed just now':` · refreshed ${Math.floor(age/60)}m ago`);
    msg.className=r.connected?'msg good':'msg';
    const mode=r.refreshable?'refreshable Clerk session':(r.connected?'legacy Bearer':'not connected');
    msg.textContent=r.connected?`Suno connected (${mode})${ageText}. Tasia can refresh JWT automatically and uses Suno API audio_url.`:`Suno not connected. Install the connector, pair it, then stay logged into suno.com.`;
    if(r.cookies_set) msg.textContent+=` Legacy cookies are also installed (${r.cookie_mode||'saved'}).`;
    return r;
  }catch(e){msg.className='msg bad';msg.textContent=e.message;return null;}
}
$('generateSunoConnector').onclick=async e=>{
  e.preventDefault();
  const msg=$('sunoAuthMsg'), field=$('sunoConnectorKey');
  msg.className='msg';msg.textContent='Generating connector key…';
  try{
    const r=await api('/api/suno/connector/generate',{method:'POST'});
    let key=String(r?.connector_key||'').trim();
    if(!key){
      const status=await api('/api/suno/auth/status');
      key=String(status?.connector_key||'').trim();
    }
    field.value=key;
    field.setAttribute('value',key);
    msg.className=key?'msg good':'msg bad';
    msg.textContent=key?`Connector key ready (${key.length} chars). Copy it into the Tasia Suno Connector.`:'Connector key generation returned an empty value.';
  }catch(err){msg.className='msg bad';msg.textContent=err.message;}
};
$('copySunoConnector').onclick=async e=>{
  e.preventDefault();const msg=$('sunoAuthMsg');let key=$('sunoConnectorKey').value.trim();
  if(!key){
    try{const r=await api('/api/suno/connector/generate',{method:'POST'});key=r.connector_key||'';$('sunoConnectorKey').value=key;}catch(err){msg.className='msg bad';msg.textContent=err.message;return;}
  }
  if(!key){msg.className='msg bad';msg.textContent='Connector key is empty. Generate it first.';return;}
  const text=`Tasia Streamer URL: ${location.origin}\nConnector key: ${key}`;
  try{await navigator.clipboard.writeText(text);msg.className='msg good';msg.textContent='Connector setup copied. Paste URL + key into the Tasia Suno Connector popup.';}
  catch{prompt('Copy these into the Tasia Suno Connector:',text);}
};
$('saveSunoClientCookie').onclick=async e=>{e.preventDefault();const msg=$('sunoAuthMsg'),client_cookie=$('sunoClientCookie').value.trim();if(!client_cookie){msg.className='msg bad';msg.textContent='Paste the Suno __client cookie first.';return;}msg.className='msg';msg.textContent='Connecting refreshable Suno session…';try{await api('/api/suno/session',jsonOpts('POST',{client_cookie,device_id:''}));$('sunoClientCookie').value='';await loadSunoAuthStatus();}catch(err){msg.className='msg bad';msg.textContent=err.message;}};
$('refreshSunoSession').onclick=async e=>{e.preventDefault();const msg=$('sunoAuthMsg');msg.className='msg';msg.textContent='Refreshing Suno JWT from Clerk…';try{await api('/api/suno/session/refresh',{method:'POST'});await loadSunoAuthStatus();}catch(err){msg.className='msg bad';msg.textContent=err.message;}};
$('clearSunoSession').onclick=async e=>{e.preventDefault();const msg=$('sunoAuthMsg');try{await api('/api/suno/session',{method:'DELETE'});await loadSunoAuthStatus();}catch(err){msg.className='msg bad';msg.textContent=err.message;}};
$('rotateSunoConnector').onclick=async e=>{e.preventDefault();const msg=$('sunoAuthMsg');if(!confirm('Rotate the connector key? The browser extension will need the new key.'))return;try{const r=await api('/api/suno/connector/rotate',{method:'POST'});$('sunoConnectorKey').value=r.connector_key||'';msg.className='msg good';msg.textContent='Connector key rotated. Update the extension with the new key.';}catch(err){msg.className='msg bad';msg.textContent=err.message;}};

// Legacy beta22 signed-cookie fallback.
$('saveSunoCookieHeader').onclick=async e=>{e.preventDefault();const msg=$('sunoAuthMsg'),cookie=$('sunoCookieHeader').value.trim();if(!cookie){msg.className='msg bad';msg.textContent='Paste the Cookie header first.';return;}msg.className='msg';msg.textContent='Saving legacy Suno cookie…';try{await api('/api/suno/cookie-header',jsonOpts('POST',{cookie}));$('sunoCookieHeader').value='';await loadSunoAuthStatus();}catch(err){msg.className='msg bad';msg.textContent=err.message;}};
$('sunoCookies').onchange=async()=>{const f=$('sunoCookies').files[0],msg=$('sunoAuthMsg');if(!f)return;const form=new FormData();form.append('file',f);msg.className='msg';msg.textContent='Uploading legacy Suno cookies.txt…';try{await api('/api/suno/cookies',{method:'POST',body:form});$('sunoCookies').value='';await loadSunoAuthStatus();}catch(err){msg.className='msg bad';msg.textContent=err.message;}};
$('clearSunoCookies').onclick=async e=>{e.preventDefault();const msg=$('sunoAuthMsg');try{await api('/api/suno/cookies',{method:'DELETE'});await loadSunoAuthStatus();}catch(err){msg.className='msg bad';msg.textContent=err.message;}};

// Settings + AI ---------------------------------------------------------------
async function loadAISettings(){
  const a=await api('/api/settings/ai');$('aiBaseUrl').value=a.base_url||'';$('aiModel').value=a.model||'';$('aiApiKey').value='';$('aiApiKey').placeholder=a.api_key_set?'Saved — leave blank to keep':'API key (optional for local endpoint)';$('aiSystemPrompt').value=a.system_prompt||'';$('aiClearKey').checked=false;return a;
}
$('settingsBtn').onclick=async()=>{
  try{
    const [s]=await Promise.all([api('/api/settings/stream'),loadAISettings(),loadCatalogSettings(),loadUniversalStatus(),loadSunoAuthStatus()]);
    $('setHost').value=s.host;$('setPort').value=s.port;$('setPassword').value='';$('setSid').value=s.sid;$('setName').value=s.name;$('setGenre').value=s.genre;$('setUrl').value=s.url;$('setBitrate').value=s.bitrate;$('setSampleRate').value=s.sample_rate;$('setPublic').checked=s.public;$('setAutoplay').checked=s.autoplay_library;$('setAutoStart').checked=s.auto_start;
    $('settingsMsg').textContent='';$('aiSettingsMsg').textContent='';$('settingsDialog').showModal();
  }catch(e){alert(e.message)}
};
$('saveSettings').onclick=async e=>{e.preventDefault();const msg=$('settingsMsg');try{await api('/api/settings/stream',jsonOpts('PUT',{host:$('setHost').value.trim(),port:Number($('setPort').value),password:$('setPassword').value,sid:Number($('setSid').value),name:$('setName').value.trim(),genre:$('setGenre').value.trim(),url:$('setUrl').value.trim(),public:$('setPublic').checked,bitrate:Number($('setBitrate').value),sample_rate:Number($('setSampleRate').value),autoplay_library:$('setAutoplay').checked,auto_start:$('setAutoStart').checked}));msg.className='msg good';msg.textContent='Stream profile saved.';refreshAll();}catch(err){msg.className='msg bad';msg.textContent=err.message;}};
$('saveAISettings').onclick=async e=>{
  e.preventDefault();const msg=$('aiSettingsMsg');msg.className='msg';msg.textContent='Saving AI settings…';
  try{
    const r=await api('/api/settings/ai',jsonOpts('PUT',{base_url:$('aiBaseUrl').value.trim(),model:$('aiModel').value.trim(),api_key:$('aiApiKey').value,system_prompt:$('aiSystemPrompt').value.trim(),clear_api_key:$('aiClearKey').checked}));
    $('aiApiKey').value='';$('aiClearKey').checked=false;$('aiApiKey').placeholder=r.settings.api_key_set?'Saved — leave blank to keep':'API key (optional for local endpoint)';msg.className='msg good';msg.textContent='DJ AI settings saved.';
  }catch(err){msg.className='msg bad';msg.textContent=err.message;}
};
$('testAI').onclick=async e=>{e.preventDefault();const msg=$('aiSettingsMsg');msg.className='msg';msg.textContent='Testing saved endpoint…';try{const r=await api('/api/ai/test',{method:'POST'});msg.className='msg good';msg.textContent=`AI replied: ${r.answer}`;}catch(err){msg.className='msg bad';msg.textContent=err.message;}};

$('aiBtn').onclick=()=>{$('aiAskMsg').textContent='';$('aiDialog').showModal();};
$('closeAI').onclick=()=>$('aiDialog').close();
[...document.querySelectorAll('[data-ai-prompt]')].forEach(b=>b.onclick=()=>{$('aiQuestion').value=b.dataset.aiPrompt;$('aiQuestion').focus();});
$('askAI').onclick=async()=>{
  const prompt=$('aiQuestion').value.trim(),msg=$('aiAskMsg');if(!prompt){msg.className='msg bad';msg.textContent='Ask something first.';return;}
  msg.className='msg';msg.textContent='Thinking about the current set…';$('askAI').disabled=true;
  try{const r=await api('/api/ai/advice',jsonOpts('POST',{prompt,folder:state.libraryPath,search:$('librarySearch').value.trim()}));$('aiAnswer').textContent=r.answer;msg.className='msg good';msg.textContent='Advice only — nothing was changed automatically.';}catch(err){msg.className='msg bad';msg.textContent=err.message;}finally{$('askAI').disabled=false;}
};

// Account/admin ---------------------------------------------------------------
$('createUser').onclick=async e=>{e.preventDefault();const msg=$('userMsg');try{const r=await api('/api/users',jsonOpts('POST',{display_name:$('newDisplay').value.trim()||null,username:$('newUser').value.trim(),password:$('newPass').value,is_admin:$('newAdmin').checked,open_after_create:$('openCreatedUser').checked}));['newDisplay','newUser','newPass'].forEach(id=>$(id).value='');if(r.switched){state.user=r.user;state.status=null;state.queue=[];state.playlist=[];state.favorites=[];state.sources=[];state.sourceRows=[];state.catalogResults=[];state.catalogSettings={};state.libraryPath='';$('settingsDialog').close();showApp();await initialLoad();history.replaceState(null,'','/app');}else{msg.className='msg good';msg.textContent=`Created ${r.user.username}. Music folder: ${r.user.music_folder}`;}}catch(err){msg.className='msg bad';msg.textContent=err.message;}};
$('changeAccountPass').onclick=async e=>{e.preventDefault();const msg=$('passMsg');try{await api('/api/account/password',jsonOpts('POST',{current_password:$('oldAccountPass').value,new_password:$('newAccountPass').value}));msg.className='msg good';msg.textContent='Password changed.';$('oldAccountPass').value='';$('newAccountPass').value='';}catch(err){msg.className='msg bad';msg.textContent=err.message;}};

setInterval(()=>{if(state.user)refreshAll();},2000);
setInterval(renderProgress,250);
boot();
