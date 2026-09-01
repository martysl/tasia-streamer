import * as btch from 'btch-downloader';

const op = process.argv[2] || '';
const value = process.argv[3] || '';
const allowed = new Set(['spotify', 'soundcloud', 'gdrive']);

function out(value) {
  process.stdout.write(JSON.stringify(value ?? null));
}

try {
  if (op === 'status') {
    const missing = [...allowed].filter((name) => typeof btch[name] !== 'function');
    out({ ok: missing.length === 0, missing });
    process.exit(missing.length ? 2 : 0);
  }
  if (!allowed.has(op)) throw new Error(`Unsupported BTCH operation: ${op}`);
  if (!value) throw new Error('Missing media URL');
  const fn = btch[op];
  if (typeof fn !== 'function') throw new Error(`btch-downloader does not export ${op}()`);
  const result = await fn(value);
  out(result);
} catch (error) {
  out({ status: false, message: error instanceof Error ? error.message : String(error) });
  process.exit(1);
}
