(() => {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  function normalizeImportLine(value) {
    return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  }

  async function readTxtInput(file, pasted) {
    if (!file) return String(pasted || '');
    const bytes = await file.arrayBuffer();
    try {
      return new TextDecoder('utf-8', {fatal: true}).decode(bytes);
    } catch (_) {
      try {
        return new TextDecoder('windows-1250').decode(bytes);
      } catch (_) {
        return new TextDecoder().decode(bytes);
      }
    }
  }

  function parsedImportEntries(text) {
    return String(text || '')
      .replace(/\r/g, '')
      .split('\n')
      .map((raw, index) => ({line: index + 1, value: raw.trim()}))
      .filter(row => row.value && !row.value.startsWith('#'));
  }

  async function refreshImportTarget(target) {
    if (target === 'favorites') {
      await loadFavorites();
      return;
    }
    await refreshAll();
  }

  function aggregateResult(target, source, entries, report) {
    const summary = {lines: entries.length, added: 0, skipped: 0, failed: 0};
    for (const row of report) {
      if (row.status === 'added') summary.added += 1;
      else if (row.status === 'skipped') summary.skipped += 1;
      else if (row.status === 'failed') summary.failed += 1;
    }
    return {ok: summary.failed === 0, target, source, summary, report};
  }

  async function importOneByOne() {
    const file = $('txtImportFile').files[0];
    const pasted = $('txtImportText').value.trim();
    const msg = $('txtImportMsg');
    const button = $('txtImportStart');
    if (!file && !pasted) {
      msg.className = 'msg bad';
      msg.textContent = 'Choose a .txt file or paste a song list.';
      return;
    }

    const target = $('txtImportTarget').value;
    const source = $('txtImportSource').value;
    const skipDuplicates = $('txtSkipDuplicates').checked;
    const continueErrors = $('txtContinueErrors').checked;

    let text;
    try {
      text = await readTxtInput(file, pasted);
    } catch (e) {
      msg.className = 'msg bad';
      msg.textContent = `Could not read TXT file: ${e.message || e}`;
      return;
    }

    const entries = parsedImportEntries(text);
    if (!entries.length) {
      msg.className = 'msg bad';
      msg.textContent = 'Song list contains no songs.';
      return;
    }
    if (entries.length > 250) {
      msg.className = 'msg bad';
      msg.textContent = 'TXT import is limited to 250 song lines at once.';
      return;
    }

    button.disabled = true;
    const originalLabel = button.textContent;
    const report = [];
    const seen = new Set();

    try {
      for (let index = 0; index < entries.length; index += 1) {
        const entry = entries[index];
        const norm = normalizeImportLine(entry.value);
        button.textContent = `Importing ${index + 1}/${entries.length}…`;

        if (skipDuplicates && norm && seen.has(norm)) {
          report.push({line: entry.line, input: entry.value, status: 'skipped', detail: 'Duplicate line in list'});
          const current = aggregateResult(target, source, entries, report);
          renderTxtImportReport(current);
          const s = current.summary;
          msg.className = 'msg';
          msg.textContent = `Resolving ${index + 1}/${entries.length} · ${s.added} added · ${s.skipped} skipped · ${s.failed} failed`;
          continue;
        }
        if (norm) seen.add(norm);

        const fd = new FormData();
        fd.append('text', entry.value);
        fd.append('target', target);
        fd.append('source', source);
        fd.append('skip_duplicates', skipDuplicates ? 'true' : 'false');
        fd.append('continue_on_error', 'true');

        try {
          const result = await api('/api/import/txt', {method: 'POST', body: fd});
          const first = (result.report || [])[0] || {
            input: entry.value,
            status: result.summary?.failed ? 'failed' : 'added'
          };
          report.push({...first, line: entry.line, input: entry.value});
        } catch (e) {
          report.push({line: entry.line, input: entry.value, status: 'failed', detail: e.message || String(e)});
        }

        // The server commits each item before returning this single-song request.
        // Refresh now so Playlist/Queue visibly grows while the remaining lines
        // are still being resolved.
        try {
          await refreshImportTarget(target);
        } catch (_) {
          // Progress rendering must not turn a successful import into a failure.
        }

        const current = aggregateResult(target, source, entries, report);
        renderTxtImportReport(current);
        const s = current.summary;
        msg.className = s.failed ? 'msg bad' : 'msg';
        msg.textContent = `Resolving ${index + 1}/${entries.length} · ${s.added} added · ${s.skipped} skipped · ${s.failed} failed`;

        if (!continueErrors && firstFailed(report)) break;
        await sleep(0);
      }

      const finalResult = aggregateResult(target, source, entries, report);
      renderTxtImportReport(finalResult);
      await Promise.all([refreshAll(), loadFavorites(), loadLibrary()]);
      const s = finalResult.summary;
      msg.className = s.failed ? 'msg bad' : 'msg good';
      msg.textContent = `Import finished: ${s.added} added · ${s.skipped} skipped · ${s.failed} failed.`;
    } finally {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  }

  function firstFailed(report) {
    return report.length > 0 && report[report.length - 1].status === 'failed';
  }

  async function queuePlaylistOneByOne() {
    const button = $('queuePlaylist');
    const originalLabel = button.textContent;
    button.disabled = true;

    try {
      await refreshAll();
      const items = [...(state.playlist || [])];
      if (!items.length) {
        alert('Playlist is empty.');
        return;
      }

      let queued = 0;
      const failed = [];
      for (let index = 0; index < items.length; index += 1) {
        const item = items[index];
        button.textContent = `Resolving ${index + 1}/${items.length}…`;
        try {
          await api(`/api/playlist/${Number(item.id)}/queue`, {method: 'POST'});
          queued += 1;
        } catch (e) {
          failed.push({id: item.id, title: item.title || `Playlist item ${item.id}`, error: e.message || String(e)});
        }

        // Each track appears in Queue immediately after its resolver/cache step.
        try { await refreshAll(); } catch (_) {}
        await sleep(0);
      }

      const suffix = failed.length ? ` ${failed.length} failed to resolve.` : '';
      alert(`Queued ${queued} playlist tracks.${suffix}`);
    } finally {
      button.disabled = false;
      button.textContent = originalLabel;
      try { await refreshAll(); } catch (_) {}
    }
  }

  function safeDownloadName(value) {
    const base = String(value || 'Suno track')
      .replace(/[\\/:*?"<>|\u0000-\u001f]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 140) || 'Suno track';
    return `${base}.mp3`;
  }

  async function downloadCachedSuno() {
    const url = $('sunoUrl')?.value.trim() || '';
    const title = $('sunoTitle')?.value.trim() || '';
    const artist = $('sunoArtist')?.value.trim() || '';
    const msg = $('sunoMsg');
    const button = $('sunoDownload');
    if (!url) {
      msg.className = 'msg bad';
      msg.textContent = 'Paste a Suno link, UUID or direct audio URL first.';
      return;
    }

    let temporaryPlaylistId = null;
    const originalLabel = button.textContent;
    button.disabled = true;
    button.textContent = 'Caching…';
    msg.className = 'msg';
    msg.textContent = 'Caching Suno audio for browser download…';

    try {
      // Reuse the exact same resolver/cache path as the normal Suno Playlist
      // button, but keep the reference only long enough to access the private
      // cached file. Nothing is sent to Queue or playback.
      const cached = await api('/api/playlist/url', jsonOpts('POST', {
        url,
        title: title || null,
        artist: artist || null
      }));
      temporaryPlaylistId = Number(cached?.playlist_id || 0) || null;
      if (!temporaryPlaylistId) throw new Error('Tasia cached the track but returned no temporary playlist id.');

      button.textContent = 'Downloading…';
      const response = await fetch(`/api/playlist/${temporaryPlaylistId}/preview`, {credentials: 'same-origin'});
      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
          const data = await response.json();
          detail = data?.detail || detail;
        } catch (_) {}
        throw new Error(detail);
      }

      const blob = await response.blob();
      if (!blob.size) throw new Error('Cached Suno file is empty.');
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = safeDownloadName(title || `Suno-${Date.now()}`);
      anchor.style.display = 'none';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 30000);

      msg.className = 'msg good';
      msg.textContent = 'Cached + downloaded in your browser. Nothing was queued or played.';
      try { await loadLibrary(); } catch (_) {}
    } catch (e) {
      msg.className = 'msg bad';
      msg.textContent = `Download failed: ${e.message || e}`;
    } finally {
      if (temporaryPlaylistId) {
        try { await api(`/api/playlist/${temporaryPlaylistId}`, {method: 'DELETE'}); } catch (_) {}
      }
      button.disabled = false;
      button.textContent = originalLabel;
      try { await refreshAll(); } catch (_) {}
    }
  }

  function installPublicSunoUi() {
    const favouriteButton = $('sunoFavorite');
    if (favouriteButton && !$('sunoDownload')) {
      const download = document.createElement('button');
      download.id = 'sunoDownload';
      download.type = 'button';
      download.className = 'ghost';
      download.textContent = '⬇ Download';
      download.title = 'Cache this Suno track, then download the cached audio in the browser without playing it';
      favouriteButton.after(download);
      download.onclick = downloadCachedSuno;
    }

    const box = $('sunoUrl')?.closest('.suno-box');
    if (box && !box.querySelector('.suno-public-note')) {
      const note = document.createElement('div');
      note.className = 'hint suno-public-note';
      note.textContent = 'Public Suno media mode: song URLs / UUIDs can cache without Tasia Suno login. Download caches first, then saves the cached MP3 through your browser.';
      box.appendChild(note);
    }

    // Keep the legacy auth controls in the DOM for backwards compatibility so
    // old app.js handlers cannot crash, but remove them from the visible Settings
    // workflow. Public clip playback no longer depends on Clerk/JWT setup.
    const connectorField = $('sunoConnectorKey');
    const authBlock = connectorField?.closest('.catalog-settings-block');
    if (authBlock) authBlock.style.display = 'none';

    // app.js asks for Suno auth status every time Settings opens. Override that
    // optional UI hook so the public-media workflow does not make any Clerk/auth
    // request just to open Settings. Legacy API routes remain available for old
    // installations, but they are no longer part of the normal workflow.
    window.loadSunoAuthStatus = async () => ({ok: true, public_media: true, connected: false});
  }

  // app.js installs the original bulk handlers first. This file loads after it
  // and intentionally replaces only the long-running actions plus the Suno
  // public-media/download workflow.
  if ($('txtImportStart')) $('txtImportStart').onclick = importOneByOne;
  if ($('queuePlaylist')) $('queuePlaylist').onclick = queuePlaylistOneByOne;
  installPublicSunoUi();
})();
