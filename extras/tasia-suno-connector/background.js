let cachedToken = null;

function makeUuid() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

async function getDeviceId() {
  const data = await chrome.storage.local.get(['deviceId']);
  if (data.deviceId) return data.deviceId;
  const deviceId = makeUuid();
  await chrome.storage.local.set({deviceId});
  return deviceId;
}

function normalizeBaseUrl(value) {
  return String(value || '').trim().replace(/\/+$/, '');
}

async function sendSession(token, reason = 'captured') {
  if (!token) return {ok:false, error:'No Suno token captured yet'};
  const data = await chrome.storage.local.get(['tasiaUrl', 'connectorKey']);
  const tasiaUrl = normalizeBaseUrl(data.tasiaUrl);
  const connectorKey = String(data.connectorKey || '').trim();
  if (!tasiaUrl || !connectorKey) return {ok:false, error:'Configure Tasia Streamer URL and connector key first'};

  const deviceId = await getDeviceId();
  try {
    const response = await fetch(`${tasiaUrl}/api/suno/connector/session`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({connector_key:connectorKey, token, device_id:deviceId})
    });
    const text = await response.text();
    let payload = {};
    try { payload = text ? JSON.parse(text) : {}; } catch { payload = {detail:text}; }
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    await chrome.storage.local.set({
      lastSync: Date.now(),
      lastError: '',
      lastReason: reason,
      connectedUsername: payload.username || ''
    });
    return {ok:true, payload};
  } catch (error) {
    await chrome.storage.local.set({lastError:String(error.message || error), lastReason:reason});
    return {ok:false, error:String(error.message || error)};
  }
}

async function captureToken(raw, reason) {
  const token = String(raw || '').replace(/^Bearer\s+/i, '').trim();
  if (!token || token.length < 20) return;
  if (token === cachedToken) return;
  cachedToken = token;
  await chrome.storage.local.set({authToken:token, lastCaptured:Date.now()});
  await sendSession(token, reason);
}

chrome.storage.local.get(['authToken']).then(data => { if (data.authToken) cachedToken = data.authToken; });

chrome.webRequest.onBeforeSendHeaders.addListener(
  details => {
    if (!details.requestHeaders) return;
    const header = details.requestHeaders.find(h => String(h.name || '').toLowerCase() === 'authorization');
    if (header && header.value) captureToken(header.value, 'Suno request');
  },
  {urls:[
    'https://studio-api-prod.suno.com/*',
    'https://auth.suno.com/*',
    'https://clerk.suno.com/*'
  ]},
  ['requestHeaders']
);

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.action === 'syncNow') {
    chrome.storage.local.get(['authToken']).then(data => sendSession(data.authToken, 'manual sync')).then(sendResponse);
    return true;
  }
  if (message?.action === 'status') {
    chrome.storage.local.get(['tasiaUrl','connectorKey','lastSync','lastError','lastCaptured','connectedUsername']).then(data => {
      sendResponse({...data, tokenCaptured:!!cachedToken});
    });
    return true;
  }
});
