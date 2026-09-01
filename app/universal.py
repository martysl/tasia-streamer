from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from .config import MAX_REMOTE_BYTES, USER_DATA_DIR
from .media import ffprobe


class UniversalError(ValueError):
    pass


YT_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be",
}
SPOTIFY_HOSTS = {"open.spotify.com", "www.open.spotify.com", "spotify.link", "www.spotify.link"}


def _cookie_path(user_id: int) -> Path:
    path = USER_DATA_DIR / str(int(user_id)) / "secrets" / "youtube-cookies.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def cookie_status(user_id: int) -> dict:
    path = _cookie_path(user_id)
    return {"cookies_set": path.exists() and path.stat().st_size > 0}


def save_cookies(user_id: int, data: bytes) -> None:
    if not data:
        raise UniversalError("The cookies.txt file is empty")
    if len(data) > 5 * 1024 * 1024:
        raise UniversalError("cookies.txt is larger than 5 MB")
    text = data.decode("utf-8", errors="replace")
    if "\t" not in text or ("Netscape HTTP Cookie File" not in text and "youtube" not in text.lower()):
        raise UniversalError("This does not look like a Netscape cookies.txt export")
    path = _cookie_path(user_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def clear_cookies(user_id: int) -> None:
    _cookie_path(user_id).unlink(missing_ok=True)


def _ydl_class():
    try:
        from yt_dlp import YoutubeDL
    except Exception as exc:
        raise UniversalError("yt-dlp is not installed in this Tasia Streamer build") from exc
    return YoutubeDL


def _ydl_opts(user_id: int, **extra) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "cachedir": str(USER_DATA_DIR / str(int(user_id)) / "yt-dlp-cache"),
    }
    cookie = _cookie_path(user_id)
    if cookie.exists() and cookie.stat().st_size:
        opts["cookiefile"] = str(cookie)
    opts.update(extra)
    return opts


def _spotify_search_text(url: str) -> str:
    try:
        r = httpx.get(
            "https://open.spotify.com/oembed",
            params={"url": url},
            follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": "Tasia-Streamer/2.0"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json() or {}
    except Exception as exc:
        raise UniversalError(f"Could not read this Spotify link: {exc}") from exc
    title = str(data.get("title") or "").strip()
    if not title:
        raise UniversalError("Spotify did not return a title for this link")
    return title


def _normalize_query(value: str) -> tuple[str, bool, str]:
    value = str(value or "").strip()
    if not value:
        raise UniversalError("Type a song, artist, YouTube URL, or Spotify URL")
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        host = parsed.hostname.lower()
        if host in YT_HOSTS:
            return value, True, value
        if host in SPOTIFY_HOSTS:
            return _spotify_search_text(value), False, value
        raise UniversalError("Universal Search accepts text, YouTube URLs, and Spotify URLs")
    return value, False, ""


def _pack(row: dict) -> str:
    raw = json.dumps(row, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unpack(value: str) -> dict:
    try:
        padded = value + "=" * ((4 - len(value) % 4) % 4)
        row = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise UniversalError("Invalid Universal Search track id") from exc
    if not isinstance(row, dict) or not row.get("video_url"):
        raise UniversalError("Invalid Universal Search track id")
    return row


def _entry_to_track(entry: dict, original_source: str = "") -> dict | None:
    if not isinstance(entry, dict):
        return None
    video_id = str(entry.get("id") or "").strip()
    webpage = str(entry.get("webpage_url") or entry.get("url") or "").strip()
    if webpage and not webpage.startswith("http") and video_id:
        webpage = f"https://www.youtube.com/watch?v={video_id}"
    if not webpage and video_id:
        webpage = f"https://www.youtube.com/watch?v={video_id}"
    parsed = urlparse(webpage)
    if not parsed.hostname or parsed.hostname.lower() not in YT_HOSTS:
        # ytsearch can occasionally hand back an extractor-specific URL. We only
        # keep YouTube results for this Bhariya-style resolver.
        if video_id:
            webpage = f"https://www.youtube.com/watch?v={video_id}"
        else:
            return None
    title = str(entry.get("title") or "Untitled").strip()
    artist = str(entry.get("artist") or entry.get("uploader") or entry.get("channel") or "").strip()
    duration = entry.get("duration")
    try:
        duration = float(duration) if duration not in (None, "") else None
        if duration is not None and (not math.isfinite(duration) or duration < 0):
            duration = None
    except (TypeError, ValueError):
        duration = None
    payload = {
        "video_url": webpage,
        "title": title,
        "artist": artist,
        "duration": duration,
        "thumbnail": str(entry.get("thumbnail") or ""),
        "source": original_source or webpage,
    }
    return {
        "provider": "universal",
        "id": _pack(payload),
        "title": title,
        "artist": artist,
        "duration": duration,
        "url": original_source or webpage,
        "artwork": payload["thumbnail"],
        "license": "Universal Search",
        "access": "playable",
    }


def search(query: str, limit: int, user_id: int) -> list[dict]:
    query, direct, original = _normalize_query(query)
    limit = max(1, min(int(limit), 15))
    YoutubeDL = _ydl_class()
    target = query if direct else f"ytsearch{limit}:{query}"
    try:
        with YoutubeDL(_ydl_opts(user_id, extract_flat="in_playlist", skip_download=True)) as ydl:
            info = ydl.extract_info(target, download=False)
    except Exception as exc:
        message = str(exc).strip() or exc.__class__.__name__
        raise UniversalError(f"Universal Search failed: {message[-700:]}") from exc
    if not info:
        return []
    entries = info.get("entries") if isinstance(info, dict) else None
    if entries is None:
        entries = [info]
    rows = []
    for entry in entries or []:
        track = _entry_to_track(entry, original_source=original)
        if track:
            rows.append(track)
        if len(rows) >= limit:
            break
    return rows


def get_track(track_id: str) -> dict:
    info = _unpack(str(track_id))
    duration = info.get("duration")
    try:
        duration = float(duration) if duration not in (None, "") else None
        if duration is not None and (not math.isfinite(duration) or duration < 0):
            duration = None
    except (TypeError, ValueError):
        duration = None
    return {
        "provider": "universal",
        "id": str(track_id),
        "title": str(info.get("title") or "Untitled"),
        "artist": str(info.get("artist") or ""),
        "duration": duration,
        "url": str(info.get("source") or info.get("video_url") or ""),
        "artwork": str(info.get("thumbnail") or ""),
        "license": "Universal Search",
        "access": "playable",
    }


DEFAULT_CONVERTER_URL = "https://yapi.is-on.click/api/convert"


def _converter_url(value: str | None) -> str:
    url = str(value or DEFAULT_CONVERTER_URL).strip() or DEFAULT_CONVERTER_URL
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise UniversalError("MP3 converter API must be an http:// or https:// URL")
    return url


def _write_limited_stream(response: httpx.Response, target: Path) -> None:
    total = 0
    with target.open("wb") as handle:
        for chunk in response.iter_bytes(1024 * 256):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_REMOTE_BYTES:
                raise UniversalError("Converted track is larger than MAX_REMOTE_MB")
            handle.write(chunk)
    if total == 0:
        raise UniversalError("MP3 converter returned an empty file")


def _download_file_url(url: str, target: Path) -> None:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise UniversalError("MP3 converter returned an invalid download URL")
    try:
        with httpx.stream(
            "GET", url, follow_redirects=True,
            headers={"Accept": "audio/mpeg,audio/*;q=0.9,*/*;q=0.1", "User-Agent": "Tasia-Streamer/2.0"},
            timeout=httpx.Timeout(300.0, connect=30.0),
        ) as response:
            response.raise_for_status()
            _write_limited_stream(response, target)
    except UniversalError:
        raise
    except Exception as exc:
        raise UniversalError(f"Could not download the converted MP3: {exc}") from exc


def _convert_with_api(video_url: str, target: Path, converter_url: str | None = None) -> None:
    endpoint = _converter_url(converter_url)
    payload = {
        "url": video_url,
        "format": "mp3",
        "quality": "best",
        "return_file": True,
    }
    target.unlink(missing_ok=True)
    try:
        with httpx.stream(
            "POST", endpoint,
            json=payload,
            follow_redirects=True,
            headers={
                "Accept": "audio/mpeg,application/octet-stream,application/json;q=0.8,*/*;q=0.1",
                "User-Agent": "Tasia-Streamer/2.0",
            },
            timeout=httpx.Timeout(600.0, connect=30.0),
        ) as response:
            if response.status_code >= 400:
                body = response.read()[:1000].decode("utf-8", errors="replace").strip()
                raise UniversalError(
                    f"MP3 converter returned HTTP {response.status_code}: {body or response.reason_phrase}"
                )

            content_type = str(response.headers.get("content-type") or "").lower()
            if "application/json" in content_type or "+json" in content_type:
                raw = response.read()
                try:
                    data = json.loads(raw.decode("utf-8", errors="replace"))
                except Exception as exc:
                    raise UniversalError("MP3 converter returned invalid JSON") from exc
                if not isinstance(data, dict):
                    raise UniversalError("MP3 converter returned JSON but no downloadable file")
                url = next((str(data.get(k) or "").strip() for k in ("file_url", "download_url", "audio_url", "url") if data.get(k)), "")
                if not url:
                    detail = str(data.get("error") or data.get("message") or "").strip()
                    raise UniversalError(detail or "MP3 converter returned JSON but no file URL")
                _download_file_url(url, target)
                return

            _write_limited_stream(response, target)
    except UniversalError:
        target.unlink(missing_ok=True)
        raise
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise UniversalError(f"MP3 converter request failed: {exc}") from exc


def cache_track(track_id: str, user_id: int, converter_url: str | None = None) -> tuple[Path, float | None]:
    info = _unpack(str(track_id))
    video_url = str(info["video_url"])
    digest = hashlib.sha256(video_url.encode("utf-8")).hexdigest()[:24]
    cache = USER_DATA_DIR / str(int(user_id)) / "cache" / "universal"
    cache.mkdir(parents=True, exist_ok=True)
    final = cache / f"{digest}.mp3"
    if final.exists():
        duration, ok = ffprobe(final)
        if ok:
            return final, duration
        final.unlink(missing_ok=True)

    raw = cache / f".{digest}.api-download"
    tmp = cache / f".{digest}.tmp.mp3"
    raw.unlink(missing_ok=True)
    tmp.unlink(missing_ok=True)
    try:
        _convert_with_api(video_url, raw, converter_url)

        # Do not trust the extension/content-type from a remote converter. FFmpeg
        # validates/normalizes whatever playable audio it returned into the exact
        # format the radio engine expects.
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw), "-vn", "-ac", "2", "-ar", "44100", "-codec:a", "libmp3lame", "-b:a", "192k", str(tmp)],
            capture_output=True, text=True, timeout=600, check=False,
        )
        if result.returncode != 0:
            raise UniversalError(f"FFmpeg could not decode the converter response: {result.stderr.strip()[-500:]}")
        duration, ok = ffprobe(tmp)
        if not ok:
            raise UniversalError("MP3 converter response did not contain playable audio")
        os.replace(tmp, final)
        return final, duration
    finally:
        raw.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)

def runtime_status(user_id: int) -> dict:
    try:
        from yt_dlp.version import __version__ as ytdlp_version
    except Exception as exc:
        raise UniversalError("yt-dlp is not installed") from exc
    deno = shutil.which("deno")
    deno_version = ""
    if deno:
        try:
            r = subprocess.run([deno, "--version"], capture_output=True, text=True, timeout=10, check=False)
            deno_version = (r.stdout.splitlines() or [""])[0].strip()
        except Exception:
            pass
    status = cookie_status(user_id)
    return {
        "ok": True,
        "yt_dlp": ytdlp_version,
        "deno": deno_version or None,
        "cookies_set": status["cookies_set"],
        "message": f"Universal search ready — yt-dlp {ytdlp_version} (search only)" + (f", {deno_version}" if deno_version else "; Deno runtime missing"),
    }
