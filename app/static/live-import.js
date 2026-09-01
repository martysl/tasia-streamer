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

  // app.js installs the original bulk handlers first. This file loads after it
  // and intentionally replaces only these two long-running actions.
  if ($('txtImportStart')) $('txtImportStart').onclick = importOneByOne;
  if ($('queuePlaylist')) $('queuePlaylist').onclick = queuePlaylistOneByOne;
})();
