from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse


class BtchError(ValueError):
    pass


HELPER = Path('/app/node/btch-helper.mjs')
PROVIDERS = {
    'btch-spotify': ('spotify', 'Spotify'),
    'btch-soundcloud': ('soundcloud', 'SoundCloud'),
    'btch-gdrive': ('gdrive', 'Google Drive'),
}


def _run(op: str, value: str = '', timeout: int = 75) -> dict:
    cmd = ['node', str(HELPER), op]
    if value:
        cmd.append(value)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise BtchError('Node.js is not installed in this Tasia Streamer image') from exc
    except subprocess.TimeoutExpired as exc:
        raise BtchError('BTCH resolver timed out') from exc
    raw = (proc.stdout or '').strip()
    if not raw:
        detail = (proc.stderr or '').strip()[-400:]
        raise BtchError(f'BTCH resolver returned no JSON{": " + detail if detail else ""}')
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BtchError('BTCH resolver returned invalid JSON') from exc
    if not isinstance(data, dict):
        raise BtchError('BTCH resolver returned an unexpected response')
    if proc.returncode != 0 or data.get('status') is False or data.get('ok') is False:
        raise BtchError(str(data.get('message') or data.get('error') or 'BTCH resolver failed'))
    return data


def runtime_status() -> dict:
    data = _run('status', timeout=15)
    if not data.get('ok'):
        raise BtchError('btch-downloader is missing required exports')
    return {'ok': True, 'message': 'btch-downloader 6.3.6 is installed and ready.'}


def pack_url(url: str) -> str:
    return base64.urlsafe_b64encode(url.encode('utf-8')).decode('ascii').rstrip('=')


def unpack_url(value: str) -> str:
    try:
        padded = value + '=' * ((4 - len(value) % 4) % 4)
        url = base64.urlsafe_b64decode(padded.encode('ascii')).decode('utf-8')
    except Exception as exc:
        raise BtchError('Invalid BTCH track id') from exc
    if not url.startswith(('http://', 'https://')):
        raise BtchError('Invalid BTCH source URL')
    return url


def _walk(value, path: tuple[str, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, path + (str(key).lower(),))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _walk(item, path + (str(i),))
    else:
        yield path, value


def _first_text(data: dict, names: tuple[str, ...]) -> str:
    wanted = {x.lower() for x in names}
    for path, value in _walk(data):
        if path and path[-1] in wanted and isinstance(value, (str, int, float)):
            text = str(value).strip()
            if text and not text.startswith(('http://', 'https://')):
                return text
    return ''


def _duration(data: dict) -> float | None:
    for path, value in _walk(data):
        if not path or path[-1] not in {'duration', 'duration_ms', 'durationms'}:
            continue
        try:
            n = float(value)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        if path[-1] in {'duration_ms', 'durationms'} or n > 10000:
            n /= 1000.0
        return n
    return None


def _artwork(data: dict) -> str:
    for path, value in _walk(data):
        if not isinstance(value, str) or not value.startswith(('http://', 'https://')):
            continue
        joined = '.'.join(path)
        if any(x in joined for x in ('thumbnail', 'artwork', 'cover', 'image', 'poster')):
            return value
    return ''


def _media_score(path: tuple[str, ...], url: str) -> int:
    joined = '.'.join(path).lower()
    low = url.lower()
    if any(x in joined for x in ('thumbnail', 'artwork', 'cover', 'image', 'avatar', 'profile')):
        return -1000
    if re.search(r'\.(mp3|m4a|aac|ogg|opus|wav|flac)(?:$|[?#])', low):
        score = 120
    else:
        score = 0
    if 'mp3' in joined:
        score += 100
    if any(x in joined for x in ('audio', 'download', 'media', 'stream')):
        score += 70
    if path and path[-1] in {'url', 'link', 'src'}:
        score += 15
    host = (urlparse(url).hostname or '').lower()
    # Page/source links are useful metadata, but should lose against actual media.
    if host in {'open.spotify.com', 'soundcloud.com', 'www.soundcloud.com', 'drive.google.com', 'docs.google.com'}:
        score -= 80
    return score


def _media_url(data: dict, source_url: str) -> str:
    candidates: list[tuple[int, str]] = []
    seen: set[str] = set()
    for path, value in _walk(data):
        if not isinstance(value, str):
            continue
        url = value.strip()
        if not url.startswith(('http://', 'https://')) or url in seen or url == source_url:
            continue
        seen.add(url)
        score = _media_score(path, url)
        if score > 0:
            candidates.append((score, url))
    candidates.sort(key=lambda row: row[0], reverse=True)
    if not candidates:
        raise BtchError('BTCH returned metadata but no downloadable audio URL')
    return candidates[0][1]


def resolve(provider: str, source_url: str) -> dict:
    provider = provider.lower()
    if provider not in PROVIDERS:
        raise BtchError('Unsupported BTCH provider')
    source_url = str(source_url or '').strip()
    parsed = urlparse(source_url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise BtchError('Paste a complete http:// or https:// URL')
    op, label = PROVIDERS[provider]
    data = _run(op, source_url)
    media_url = _media_url(data, source_url)
    title = _first_text(data, ('title', 'name', 'filename', 'track_name', 'song')) or Path(parsed.path).name or label
    artist = _first_text(data, ('artist', 'author', 'uploader', 'username', 'channel', 'owner'))
    return {
        'provider': provider,
        'id': pack_url(source_url),
        'title': title,
        'artist': artist,
        'duration': _duration(data),
        'url': source_url,
        'artwork': _artwork(data),
        'license': '',
        'access': 'playable',
        'media_url': media_url,
    }
