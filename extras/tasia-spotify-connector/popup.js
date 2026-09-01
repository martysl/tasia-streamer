const $ = id => document.getElementById(id);

function normalizeBaseUrl(value) {
  return String(value || '').trim().replace(/\/+$/, '');
}

function originPattern(value) {
  const url = new URL(normalizeBaseUrl(value));
  if (!['http:','https:'].includes(url.protocol)) throw new Error('Streamer URL must use http:// or https://');
  return `${url.origin}/*`;
}

function fmtTime(ms) {
  const value = Number(ms || 0);
  if (!value) return 'not captured';
  const left = Math.max(0, Math.round((value - Date.now()) / 60000));
  return left > 0 ? `${left} min left` : 'expired';
}

function message(text, good = false) {
  $('message').className = `message ${good ? 'good' : 'bad'}`;
  $('message').textContent = text || '';
}

async function requestStreamerPermission(url) {
  const origins = [originPattern(url)];
  const has = await chrome.permissions.contains({origins});
  if (has) return true;
  return chrome.permissions.request({origins});
}

async function load() {
  const data = await chrome.storage.local.get([
    'tasiaUrl','connectorKey','autoRefresh','tokenExpiresAt','lastSync','lastError',
    'connectedUsername','lastServerStatus','lastCaptured'
  ]);
  $('tasiaUrl').value = data.tasiaUrl || '';
  $('connectorKey').value = data.connectorKey || '';
  $('autoRefresh').checked = data.autoRefresh !== false;
  $('tokenStatus').textContent = fmtTime(data.tokenExpiresAt);
  const st = data.lastServerStatus || {};
  $('serverStatus').textContent = st.connected ? `${st.username || data.connectedUsername || 'connected'} · ${Math.max(1, Math.round((st.valid_for_seconds || 0)/60))} min` : (st.needs_refresh ? 'needs token refresh' : 'not connected');
  $('cacheStatus').textContent = Number.isFinite(Number(st.cache_entries)) ? `${Number(st.cache_entries)} searches` : '—';
  if (data.lastError) message(data.lastError, false);
}

async function saveSettings() {
  const tasiaUrl = normalizeBaseUrl($('tasiaUrl').value);
  const connectorKey = $('connectorKey').value.trim();
  if (!tasiaUrl) throw new Error('Enter your Tasia Streamer URL');
  if (connectorKey.length < 20) throw new Error('Paste your Tasia connector key');
  const granted = await requestStreamerPermission(tasiaUrl);
  if (!granted) throw new Error('Permission for your Tasia Streamer URL was not granted');
  await chrome.storage.local.set({tasiaUrl, connectorKey, autoRefresh:$('autoRefresh').checked});
  return {tasiaUrl, connectorKey};
}

$('save').onclick = async () => {
  try {
    await saveSettings();
    message('Saved. The connector will keep Spotify search refreshed.', true);
    chrome.runtime.sendMessage({action:'serverStatus'}, () => setTimeout(load, 250));
  } catch (e) { message(e.message || String(e)); }
};

$('sync').onclick = async () => {
  try {
    await saveSettings();
    message('Syncing current Spotify token…', true);
    chrome.runtime.sendMessage({action:'syncNow'}, result => {
      if (chrome.runtime.lastError) return message(chrome.runtime.lastError.message);
      if (!result?.ok) return message(result?.error || 'No current token. Press Refresh token now.');
      message(result.payload ? 'Spotify token synced to Tasia.' : 'Spotify refresh started. Keep your Spotify account logged in.', true);
      setTimeout(load, 800);
    });
  } catch (e) { message(e.message || String(e)); }
};

$('refresh').onclick = async () => {
  try {
    await saveSettings();
    message('Refreshing via Spotify web player…', true);
    chrome.runtime.sendMessage({action:'refreshNow'}, result => {
      if (chrome.runtime.lastError) return message(chrome.runtime.lastError.message);
      if (!result?.ok) return message(result?.error || 'Could not start Spotify refresh');
      message('Spotify helper started. A fresh token will sync automatically when the web player makes its API request.', true);
      setTimeout(load, 1200);
    });
  } catch (e) { message(e.message || String(e)); }
};

$('openSpotify').onclick = () => chrome.tabs.create({url:'https://open.spotify.com/', active:true});
$('autoRefresh').onchange = () => chrome.storage.local.set({autoRefresh:$('autoRefresh').checked});

load();
