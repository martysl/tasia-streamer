from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import btch, catalogs
from .config import USER_DATA_DIR

CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
CACHE_MAX_ENTRIES = 500

_client = None
_client_lock = threading.RLock()
_cache_lock = threading.RLock()
_installed = False


def _cache_path(settings: dict) -> Path:
    user_id = int(settings.get("user_id") or 0)
    root = USER_DATA_DIR / str(user_id if user_id > 0 else 0) / "cache"
    root.mkdir(parents=True, exist_ok=True)
    return root / "spotify-search-v1.json"


def _cache_key(query: str) -> str:
    return re.sub(r"\s+", " ", str(query or "").strip().casefold())


def _load_cache(settings: dict) -> dict:
    path = _cache_path(settings)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"version": 1, "entries": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), dict):
        return {"version": 1, "entries": {}}
    return payload


def _save_cache(settings: dict, payload: dict) -> None:
    path = _cache_path(settings)
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        entries = {}
        payload = {"version": 1, "entries": entries}

    now = time.time()
    valid = []
    for key, value in entries.items():
        if not isinstance(value, dict):
            continue
        saved_at = float(value.get("saved_at") or 0)
        rows = value.get("rows")
        if saved_at <= 0 or not isinstance(rows, list):
            continue
        if now - saved_at <= CACHE_TTL_SECONDS:
            valid.append((saved_at, key, {"saved_at": saved_at, "rows": rows[:50]}))

    valid.sort(reverse=True)
    payload = {
        "version": 1,
        "entries": {key: value for _, key, value in valid[:CACHE_MAX_ENTRIES]},
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _cached_search(settings: dict, query: str, limit: int) -> list[dict] | None:
    key = _cache_key(query)
    if not key:
        return []
    with _cache_lock:
        payload = _load_cache(settings)
        row = payload.get("entries", {}).get(key)
        if not isinstance(row, dict):
            return None
        saved_at = float(row.get("saved_at") or 0)
        rows = row.get("rows")
        if saved_at <= 0 or not isinstance(rows, list):
            return None
        if time.time() - saved_at > CACHE_TTL_SECONDS:
            return None
        return [dict(item) for item in rows[:limit] if isinstance(item, dict)]


def _store_search(settings: dict, query: str, rows: list[dict]) -> None:
    key = _cache_key(query)
    if not key:
        return
    with _cache_lock:
        payload = _load_cache(settings)
        entries = payload.setdefault("entries", {})
        entries[key] = {"saved_at": time.time(), "rows": rows[:50]}
        _save_cache(settings, payload)


def _free_client(*, reset: bool = False):
    global _client
    with _client_lock:
        if reset:
            _client = None
        if _client is None:
            try:
                from SpotipyFree import Spotify as FreeSpotify
            except Exception as exc:
                raise catalogs.CatalogError(
                    f"Spotify search backend import failed: {type(exc).__name__}: {exc}"
                ) from exc
            try:
                _client = FreeSpotify()
            except Exception as exc:
                raise catalogs.CatalogError(
                    f"Spotify search backend could not start: {type(exc).__name__}: {exc}"
                ) from exc
        return _client


def _spotify_track(row: dict) -> dict:
    artists = row.get("artists") or []
    artist = ", ".join(
        str(item.get("name") or "")
        for item in artists
        if isinstance(item, dict) and item.get("name")
    )
    album = row.get("album") or {}
    images = album.get("images") or []
    artwork = ""
    if images and isinstance(images[0], dict):
        artwork = str(images[0].get("url") or "")
    track_id = str(row.get("id") or row.get("track_id") or "").strip()
    url = str((row.get("external_urls") or {}).get("spotify") or "").strip()
    if not url and track_id:
        url = f"https://open.spotify.com/track/{track_id}"
    if not track_id and url:
        track_id = url.rstrip("/").split("/")[-1].split("?")[0]
    if not url or not track_id:
        raise ValueError("Spotify search returned a result without a usable track URL")
    duration_ms = row.get("duration_ms")
    try:
        duration = float(duration_ms or 0) / 1000.0 or None
    except (TypeError, ValueError):
        duration = None
    return {
        "provider": "btch-spotify",
        "id": btch.pack_url(url),
        "title": str(row.get("name") or "Untitled"),
        "artist": artist,
        "duration": duration,
        "url": url,
        "artwork": artwork,
        "license": "Spotify public metadata / BTCH resolver",
        "access": "playable",
    }


def _live_search(query: str, limit: int) -> list[dict]:
    last_error = None
    for attempt in range(2):
        client = _free_client(reset=attempt > 0)
        try:
            payload = client.search(query, type="track")
            items = ((payload or {}).get("tracks") or {}).get("items") or []
            rows = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    rows.append(_spotify_track(item))
                except Exception:
                    continue
                if len(rows) >= max(1, min(int(limit), 50)):
                    break
            return rows
        except Exception as exc:
            last_error = exc
    raise catalogs.CatalogError(
        f"Spotify public search failed after refreshing the free session: {last_error}"
    )


def spotify_search(settings: dict, query: str, limit: int = 30) -> list[dict]:
    query = str(query or "").strip()
    limit = max(1, min(int(limit), 50))
    if not query:
        return []

    cached = _cached_search(settings, query, limit)
    if cached is not None:
        return cached

    rows = _live_search(query, limit)
    _store_search(settings, query, rows)
    return rows


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True

    original_search = catalogs.search
    original_test = catalogs.test

    def search(provider: str, settings: dict, query: str, limit: int = 30) -> list[dict]:
        provider = str(provider or "").lower()
        if provider != "btch-spotify":
            return original_search(provider, settings, query, limit)

        raw = str(query or "").strip()
        parsed = urlparse(raw)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return [btch.resolve(provider, raw)]
        return spotify_search(settings, raw, limit)

    def test(provider: str, settings: dict) -> dict:
        provider = str(provider or "").lower()
        if provider != "btch-spotify":
            return original_test(provider, settings)

        btch_status = btch.runtime_status()
        rows = spotify_search(settings, "Daft Punk One More Time", 1)
        return {
            "ok": True,
            "message": (
                "Spotify search works through SpotipyFree without Spotify API credentials; "
                f"{len(rows)} test result. BTCH resolver: "
                f"{btch_status.get('message') or 'ready'}"
            ),
        }

    catalogs.search = search
    catalogs.test = test
