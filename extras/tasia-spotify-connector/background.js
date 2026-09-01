let cachedToken = null;
let cachedExpiresAt = 0;
let helperTabId = null;
let helperTabOwned = false;
let lastAttemptToken = null;
let lastAttemptAt = 0;

function normalizeBaseUrl(value) {
  return String(value || '').trim().replace(/\/+$/, '');
}

function tokenExpiryMs(token) {
  try {
    const parts = String(token || '').split('.');
    if (parts.length !== 3) return 0;
    let payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    payload += '='.repeat((4 - payload.length % 4) % 4);
    const data = JSON.parse(atob(payload));
    const exp = Number(data.exp || 0);
    return exp > 0 ? exp * 1000 : 0;
  } catch {
    return 0;
  }
}

async function getConfig() {
  return chrome.storage.local.get([
    'tasiaUrl', 'connectorKey', 'autoRefresh', 'authToken', 'tokenExpiresAt',
    'lastSync', 'lastError', 'lastReason', 'connectedUsername', 'lastServerStatus'
  ]);
}

async function sendSession(token, expiresAtMs, reason = 'captured') {
  const clean = String(token || '').replace(/^Bearer\s+/i, '').trim();
  if (!clean) return {ok:false, error:'No Spotify token captured yet'};

  const data = await getConfig();
  const tasiaUrl = normalizeBaseUrl(data.tasiaUrl);
  const connectorKey = String(data.connectorKey || '').trim();
  if (!tasiaUrl || !connectorKey) {
    return {ok:false, error:'Configure Tasia Streamer URL and connector key first'};
  }

  try {
    const response = await fetch(`${tasiaUrl}/api/spotify/connector/session`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        connector_key: connectorKey,
        token: clean,
        expires_at: expiresAtMs ? expiresAtMs / 1000 : null,
        reason
      })
    });
    const text = await response.text();
    let payload = {};
    try { payload = text ? JSON.parse(text) : {}; } catch { payload = {detail:text}; }
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);

    await chrome.storage.local.set({
      lastSync: Date.now(),
      lastError: '',
      lastReason: reason,
      connectedUsername: payload.username || '',
      lastServerStatus: payload
    });

    if (helperTabOwned && helperTabId !== null) {
      const closingId = helperTabId;
      helperTabId = null;
      helperTabOwned = false;
      setTimeout(() => chrome.tabs.remove(closingId).catch(() => {}), 2500);
    }
    return {ok:true, payload};
  } catch (error) {
    const message = String(error?.message || error);
    await chrome.storage.local.set({lastError:message, lastReason:reason});
    return {ok:false, error:message};
  }
}

async function captureToken(raw, reason = 'Spotify API request') {
  const token = String(raw || '').replace(/^Bearer\s+/i, '').trim();
  if (!token || token.length < 20) return;

  let expiry = tokenExpiryMs(token);
  if (!expiry || expiry <= Date.now() + 30_000) expiry = Date.now() + 55 * 60 * 1000;

  const same = token === cachedToken;
  cachedToken = token;
  cachedExpiresAt = expiry;
  await chrome.storage.local.set({authToken:token, tokenExpiresAt:expiry, lastCaptured:Date.now()});

  const data = await getConfig();
  const now = Date.now();
  const lastSync = Number(data.lastSync || 0);
  const shouldSync = !same || now - lastSync > 5 * 60 * 1000;
  const retryAllowed = token !== lastAttemptToken || now - lastAttemptAt > 60_000;
  if (shouldSync && retryAllowed) {
    lastAttemptToken = token;
    lastAttemptAt = now;
    await sendSession(token, expiry, reason);
  }
}

async function backendStatus() {
  const data = await getConfig();
  const tasiaUrl = normalizeBaseUrl(data.tasiaUrl);
  const connectorKey = String(data.connectorKey || '').trim();
  if (!tasiaUrl || !connectorKey) return {ok:false, configured:false};

  try {
    const response = await fetch(`${tasiaUrl}/api/spotify/connector/status`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({connector_key:connectorKey})
    });
    const text = await response.text();
    let payload = {};
    try { payload = text ? JSON.parse(text) : {}; } catch { payload = {detail:text}; }
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    await chrome.storage.local.set({lastServerStatus:payload, connectedUsername:payload.username || ''});
    return payload;
  } catch (error) {
    const message = String(error?.message || error);
    await chrome.storage.local.set({lastError:message});
    return {ok:false, error:message};
  }
}

async function wakeSpotify(reason = 'auto refresh') {
  try {
    const tabs = await chrome.tabs.query({url:'https://open.spotify.com/*'});
    const reusable = tabs.find(t => !t.active) || null;
    if (reusable?.id !== undefined) {
      helperTabId = reusable.id;
      helperTabOwned = false;
      await chrome.tabs.reload(reusable.id);
      await chrome.storage.local.set({lastReason:`${reason}: refreshed existing Spotify tab`});
      return {ok:true, tabId:reusable.id, reused:true};
    }

    const tab = await chrome.tabs.create({url:'https://open.spotify.com/', active:false});
    helperTabId = tab.id ?? null;
    helperTabOwned = helperTabId !== null;
    await chrome.storage.local.set({lastReason:`${reason}: opened helper Spotify tab`});
    return {ok:true, tabId:helperTabId, reused:false};
  } catch (error) {
    const message = String(error?.message || error);
    await chrome.storage.local.set({lastError:message, lastReason:reason});
    return {ok:false, error:message};
  }
}

async function refreshIfNeeded(force = false) {
  const data = await getConfig();
  const tasiaUrl = normalizeBaseUrl(data.tasiaUrl);
  const connectorKey = String(data.connectorKey || '').trim();
  if (!tasiaUrl || !connectorKey) return {ok:true, configured:false};
  if (data.autoRefresh === false && !force) return {ok:true, skipped:true};

  const status = await backendStatus();
  if (!force && status.ok && !status.needs_refresh) return {ok:true, status};

  const token = String(data.authToken || cachedToken || '').trim();
  const expiry = Number(data.tokenExpiresAt || cachedExpiresAt || 0);
  if (!force && token && expiry > Date.now() + 2 * 60 * 1000) {
    const synced = await sendSession(token, expiry, 'cached token resync');
    if (synced.ok) return synced;
  }

  return wakeSpotify(force ? 'manual refresh' : 'token refresh needed');
}

chrome.storage.local.get(['authToken','tokenExpiresAt']).then(data => {
  cachedToken = data.authToken || null;
  cachedExpiresAt = Number(data.tokenExpiresAt || 0);
});

chrome.webRequest.onBeforeSendHeaders.addListener(
  details => {
    const headers = details.requestHeaders || [];
    const auth = headers.find(h => String(h.name || '').toLowerCase() === 'authorization');
    if (auth?.value) captureToken(auth.value, 'Spotify web-player API request');
  },
  {urls:[
    'https://api.spotify.com/*',
    'https://api-partner.spotify.com/*',
    'https://spclient.wg.spotify.com/*'
  ]},
  ['requestHeaders', 'extraHeaders']
);

chrome.alarms.create('tasia-spotify-refresh', {periodInMinutes:10});
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === 'tasia-spotify-refresh') refreshIfNeeded(false);
});
chrome.runtime.onStartup.addListener(() => refreshIfNeeded(false));
chrome.runtime.onInstalled.addListener(() => refreshIfNeeded(false));

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.action === 'syncNow') {
    getConfig().then(async data => {
      const token = String(data.authToken || cachedToken || '').trim();
      const expiry = Number(data.tokenExpiresAt || cachedExpiresAt || 0);
      if (token && expiry > Date.now() + 60_000) return sendSession(token, expiry, 'manual sync');
      return refreshIfNeeded(true);
    }).then(sendResponse);
    return true;
  }
  if (message?.action === 'refreshNow') {
    refreshIfNeeded(true).then(sendResponse);
    return true;
  }
  if (message?.action === 'serverStatus') {
    backendStatus().then(sendResponse);
    return true;
  }
  if (message?.action === 'status') {
    getConfig().then(data => sendResponse({
      ...data,
      tokenCaptured: !!(data.authToken || cachedToken),
      tokenExpiresAt: Number(data.tokenExpiresAt || cachedExpiresAt || 0)
    }));
    return true;
  }
});
