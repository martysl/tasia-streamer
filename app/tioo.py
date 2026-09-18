from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

from . import btch


class TiooError(ValueError):
    pass


def _base() -> str:
    return str(os.getenv("TIOO_API_BASE", "https://backend1.tioo.eu.org") or "").strip().rstrip("/")


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _pick(data: dict, *names: str) -> str:
    wanted = {x.lower() for x in names}
    for row in _walk(data):
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            if str(key).lower() in wanted and isinstance(value, (str, int, float)):
                text = str(value).strip()
                if text:
                    return text
    return ""


def resolve_soundcloud(source_url: str) -> dict:
    source_url = str(source_url or "").strip()
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise TiooError("Paste a complete SoundCloud http:// or https:// URL")
    if not (host == "soundcloud.com" or host.endswith(".soundcloud.com")):
        raise TiooError("This resolver accepts SoundCloud URLs only")

    endpoint = _base() + "/api/downloader/soundcloud"
    try:
        response = httpx.get(
            endpoint,
            params={"url": source_url},
            headers={
                "Accept": "application/json",
                "User-Agent": "Tasia-Streamer/2.0",
            },
            follow_redirects=True,
            timeout=httpx.Timeout(45.0, connect=15.0),
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise TiooError(f"Tioo SoundCloud connection failed: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise TiooError("Tioo SoundCloud returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise TiooError("Tioo SoundCloud returned an unexpected response")
    if data.get("status") is False or data.get("success") is False:
        raise TiooError(str(data.get("message") or data.get("error") or "Tioo SoundCloud failed"))

    media_url = _pick(data, "audio") or _pick(data, "downloadMp3", "download_mp3")
    if not media_url.startswith(("http://", "https://")):
        raise TiooError("Tioo SoundCloud returned no playable audio/downloadMp3 URL")

    title = _pick(data, "title", "name") or "SoundCloud track"
    artwork = _pick(data, "thumbnail", "artwork", "downloadArtwork")
    artist = _pick(data, "artist", "author", "uploader", "username", "creator")

    return {
        "provider": "btch-soundcloud",
        "id": btch.pack_url(source_url),
        "title": title,
        "artist": artist,
        "duration": None,
        "url": source_url,
        "artwork": artwork if artwork.startswith(("http://", "https://")) else "",
        "license": "SoundCloud / Tioo resolver",
        "access": "playable",
        "media_url": media_url,
    }


def runtime_status() -> dict:
    return {
        "ok": True,
        "message": f"Tioo SoundCloud resolver configured at {_base()}/api/downloader/soundcloud",
    }
