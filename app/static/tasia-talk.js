(() => {
  if (window.__tasiaTalkUIInstalled) return;
  window.__tasiaTalkUIInstalled = true;

  const byId = id => document.getElementById(id);
  const apiCall = (path, opts={}) => fetch(path, {credentials:'same-origin', ...opts}).then(async r => {
    const text=await r.text(); let data=null; try{data=text?JSON.parse(text):null}catch{data=text}
    if(!r.ok) throw new Error(data?.detail||data||`HTTP ${r.status}`); return data;
  });
  const json = (method, body) => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});

  function quickPayload(settings, enabled){
    return {
      enabled:!!enabled,
      mesh_enabled:settings.mesh_enabled!==false,
      every_n_tracks:Number(settings.every_n_tracks||1),
      max_words:Number(settings.max_words||28),
      voice:String(settings.voice||'en-US-AnaNeural'),
      rate:String(settings.rate||'+20%'),
      pitch:String(settings.pitch||'+50Hz'),
      volume:String(settings.volume||'+0%'),
      persona_prompt:String(settings.persona_prompt||'')
    };
  }

  function injectQuickToggle(){
    if(byId('tasiaTalkQuickToggle')) return;
    const skip=byId('skip');
    if(!skip) return;
    const wrap=document.createElement('label');
    wrap.id='tasiaTalkQuickWrap';
    wrap.className='tasia-talk-quick';
    wrap.title='Turn automatic Tasia between-song comments on or off';
    wrap.innerHTML='<input id="tasiaTalkQuickToggle" type="checkbox" disabled><span>Tasia Talk</span>';
    skip.insertAdjacentElement('afterend',wrap);
    byId('tasiaTalkQuickToggle').addEventListener('change',quickToggleChanged);
  }

  function syncQuickToggle(settings){
    const quick=byId('tasiaTalkQuickToggle');
    if(quick){
      quick.checked=!!settings.enabled;
      quick.disabled=false;
    }
    const full=byId('talkEnabled');
    if(full) full.checked=!!settings.enabled;
  }

  async function refreshQuickToggle(){
    injectQuickToggle();
    const quick=byId('tasiaTalkQuickToggle');
    if(!quick) return;
    try{
      const settings=await apiCall('/api/settings/tasia-talk');
      syncQuickToggle(settings);
    }catch(_){
      quick.disabled=true;
    }
  }

  async function quickToggleChanged(e){
    const quick=e.currentTarget;
    const wanted=!!quick.checked;
    quick.disabled=true;
    try{
      const current=await apiCall('/api/settings/tasia-talk');
      const saved=await apiCall('/api/settings/tasia-talk',json('PUT',quickPayload(current,wanted)));
      syncQuickToggle(saved);
    }catch(err){
      quick.checked=!wanted;
      alert('Tasia Talk toggle failed: '+err.message);
    }finally{
      quick.disabled=false;
    }
  }

  function injectPanel(){
    if(byId('tasiaTalkBlock')) return;
    const aiMsg=byId('aiSettingsMsg');
    if(!aiMsg) return;
    const block=document.createElement('div');
    block.id='tasiaTalkBlock';
    block.innerHTML=`
      <hr><span class="label">TASIA TALK — RADIO VOICE + SL/OPENSIM CHAT BRIDGE</span>
      <p class="settings-note">Tasia can make short AI comments between songs. LSL can also send local chat or visitor-arrival events here: Tasia AI creates one reply, Edge TTS queues that reply onto the radio stream, and the exact same text is returned to the LSL object for local chat. Default voice: <code>en-US-AnaNeural</code>, rate <code>+20%</code>, pitch <code>+50Hz</code>.</p>
      <div class="settings-grid">
        <label class="check"><input id="talkEnabled" type="checkbox"> Speak between songs</label>
        <label class="check"><input id="talkMeshEnabled" type="checkbox"> Enable LSL chat / greeting bridge</label>
        <label>Speak every N tracks<input id="talkEvery" type="number" min="1" max="20" value="1"></label>
        <label>Maximum words<input id="talkMaxWords" type="number" min="8" max="80" value="28"></label>
        <label>Edge TTS voice<input id="talkVoice" value="en-US-AnaNeural"></label>
        <label>Rate<input id="talkRate" value="+20%"></label>
        <label>Pitch<input id="talkPitch" value="+50Hz"></label>
        <label>Volume<input id="talkVolume" value="+0%"></label>
        <label class="full-span">Tasia spoken persona prompt<textarea id="talkPersona" rows="3" placeholder="Optional — leave blank for Tasia's built-in warm/playful radio + SL persona"></textarea></label>
        <label class="full-span">LSL bridge API key<input id="talkApiKey" readonly></label>
      </div>
      <div class="button-row">
        <button id="saveTasiaTalk" type="button" class="ghost">Save Tasia Talk</button>
        <button id="testTasiaTalk" type="button" class="ghost">▶ Test voice + AI</button>
        <button id="rotateTasiaTalkKey" type="button" class="ghost">Rotate LSL key</button>
        <button id="copyTasiaTalkMesh" type="button" class="ghost">Copy LSL config</button>
        <a id="downloadTasiaTalkLsl" class="button ghost" href="/api/tasia-talk/lsl" download="TasiaTalkMesh.lsl">Download LSL</a>
      </div>
      <div class="hint">LSL-triggered speech never interrupts the song currently on air. It is inserted at the next safe scheduler transition; if an automatic Tasia comment was waiting, the live LSL reply takes priority.</div>
      <div id="tasiaTalkMsg" class="msg"></div>
      <audio id="tasiaTalkPreview" class="hidden" controls></audio>
    `;
    aiMsg.insertAdjacentElement('afterend', block);

    byId('saveTasiaTalk').addEventListener('click', saveSettings);
    byId('testTasiaTalk').addEventListener('click', testVoice);
    byId('rotateTasiaTalkKey').addEventListener('click', rotateKey);
    byId('copyTasiaTalkMesh').addEventListener('click', copyMeshConfig);
  }

  function msg(text, good=false, bad=false){
    const el=byId('tasiaTalkMsg'); if(!el)return;
    el.className=`msg${good?' good':''}${bad?' bad':''}`; el.textContent=text||'';
  }

  async function loadSettings(){
    injectPanel();
    try{
      const s=await apiCall('/api/settings/tasia-talk');
      syncQuickToggle(s);
      byId('talkMeshEnabled').checked=s.mesh_enabled!==false;
      byId('talkEvery').value=s.every_n_tracks||1;
      byId('talkMaxWords').value=s.max_words||28;
      byId('talkVoice').value=s.voice||'en-US-AnaNeural';
      byId('talkRate').value=s.rate||'+20%';
      byId('talkPitch').value=s.pitch||'+50Hz';
      byId('talkVolume').value=s.volume||'+0%';
      byId('talkPersona').value=s.persona_prompt||'';
      byId('talkApiKey').value=s.api_key||'';
      const bits=[];
      if(s.last_text) bits.push(`Last line: ${s.last_text}`);
      if(s.last_error) bits.push(`Last error: ${s.last_error}`);
      msg(bits.join(' · '), !!s.last_text && !s.last_error, !!s.last_error);
    }catch(e){ msg(e.message,false,true); }
  }

  async function saveSettings(e){
    e?.preventDefault(); msg('Saving Tasia Talk…');
    try{
      const s=await apiCall('/api/settings/tasia-talk',json('PUT',{
        enabled:byId('talkEnabled').checked,
        mesh_enabled:byId('talkMeshEnabled').checked,
        every_n_tracks:Number(byId('talkEvery').value||1),
        max_words:Number(byId('talkMaxWords').value||28),
        voice:byId('talkVoice').value.trim()||'en-US-AnaNeural',
        rate:byId('talkRate').value.trim()||'+20%',
        pitch:byId('talkPitch').value.trim()||'+50Hz',
        volume:byId('talkVolume').value.trim()||'+0%',
        persona_prompt:byId('talkPersona').value.trim()
      }));
      byId('talkApiKey').value=s.api_key||'';
      syncQuickToggle(s);
      msg(`Saved. Between-song talk ${s.enabled?'enabled':'disabled'}; LSL bridge ${s.mesh_enabled?'enabled':'disabled'}.`,true,false);
    }catch(err){msg(err.message,false,true)}
  }

  async function testVoice(e){
    e?.preventDefault(); msg('Tasia is thinking and warming up her mic…');
    const b=byId('testTasiaTalk'); b.disabled=true;
    try{
      const r=await apiCall('/api/tasia-talk/test',{method:'POST'});
      msg(r.text||'Tasia Talk test ready.',true,false);
      const audio=byId('tasiaTalkPreview'); audio.classList.remove('hidden'); audio.src=`${r.audio_url}?v=${Date.now()}`; audio.load();
      try{await audio.play()}catch{}
    }catch(err){msg(err.message,false,true)}finally{b.disabled=false}
  }

  async function rotateKey(e){
    e?.preventDefault();
    if(!confirm('Rotate the Tasia Talk LSL API key? Existing LSL scripts will need the new key.'))return;
    try{const r=await apiCall('/api/tasia-talk/key/rotate',{method:'POST'});byId('talkApiKey').value=r.api_key||'';msg('LSL API key rotated. Update TasiaTalkMesh.lsl.',true,false)}
    catch(err){msg(err.message,false,true)}
  }

  async function copyMeshConfig(e){
    e?.preventDefault();
    const key=byId('talkApiKey').value.trim();
    const text=`API_BASE = ${location.origin}\nAPI_KEY = ${key}`;
    try{await navigator.clipboard.writeText(text);msg('Streamer URL + LSL API key copied. Paste them into TasiaTalkMesh.lsl.',true,false)}
    catch{prompt('Copy into TasiaTalkMesh.lsl:',text)}
  }

  injectPanel();
  injectQuickToggle();

  // app.js may still be finishing login/session restoration when this helper
  // loads. As soon as the workstation is visible, mirror the persisted radio
  // talk state into the transport-bar checkbox next to Skip.
  let quickSyncTries=0;
  const quickSyncTimer=setInterval(()=>{
    quickSyncTries+=1;
    const shell=byId('appShell');
    if(shell && !shell.classList.contains('hidden')){
      clearInterval(quickSyncTimer);
      refreshQuickToggle();
    }else if(quickSyncTries>=60){
      clearInterval(quickSyncTimer);
    }
  },500);

  const settingsButton=byId('settingsBtn');
  if(settingsButton){
    const original=settingsButton.onclick;
    settingsButton.onclick=async function(ev){
      if(original) await original.call(this,ev);
      await loadSettings();
    };
  }
})();
