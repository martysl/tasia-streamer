from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from . import btch, catalogs, db
from .config import USER_DATA_DIR
from .media import get_suno_connector_key


SPOTIFY_PROVIDER = "btch-spotify"
TOKEN_SKEW_SECONDS = 45
REFRESH_AHEAD_SECONDS = 12 * 60
SEARCH_CACHE_SECONDS = 7 * 24 * 60 * 60
MAX_CACHE_ENTRIES = 250


class SpotifyConnectorIn(BaseModel):
    connector_key: str = Field(min_length=20, max_length=256)
    token: str = Field(min_length=20, max_length=8192)
    expires_at: float | None = None
    reason: str = "captured"


class SpotifyConnectorStatusIn(BaseModel):
    connector_key: str = Field(min_length=20, max_length=256)


def _user_root(user_id: int) -> Path:
    root = USER_DATA_DIR / str(int(user_id))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _token_path(user_id: int) -> Path:
    path = _user_root(user_id) / "secrets" / "spotify-token.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _cache_path(user_id: int) -> Path:
    path = _user_root(user_id) / "spotify-search-cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_json(path: Path, default: dict | None = None) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else (default or {})
    except Exception:
        return default or {}


def _find_user_by_connector_key(connector_key: str) -> dict | None:
    key = str(connector_key or "").strip()
    if not key:
        return None
    for user in db.list_users():
        try:
            candidate = get_suno_connector_key(int(user["id"]))
        except Exception:
            continue
        if candidate and secrets.compare_digest(candidate, key):
            return user
    return None


def _token_state(user_id: int) -> dict:
    data = _read_json(_token_path(user_id))
    now = time.time()
    token = str(data.get("access_token") or "").strip()
    expires_at = float(data.get("expires_at") or 0)
    return {
        "connected": bool(token and expires_at > now + TOKEN_SKEW_SECONDS),
        "needs_refresh": not token or expires_at <= now + REFRESH_AHEAD_SECONDS,
        "expires_at": expires_at or None,
        "valid_for_seconds": max(0, int(expires_at - now)) if expires_at else 0,
        "updated_at": float(data.get("updated_at") or 0) or None,
        "reason": str(data.get("reason") or ""),
    }


def _save_token(user_id: int, token: str, expires_at: float | None, reason: str = "captured") -> dict:
    now = time.time()
    clean = str(token or "").replace("Bearer ", "", 1).strip()
    if len(clean) < 20:
        raise ValueError("Spotify connector did not provide a usable Bearer token")
    expiry = float(expires_at or 0)
    # Spotify web/developer tokens are normally about one hour. If the browser
    # cannot decode the expiry, use 55 minutes rather than pretending it lasts 7 days.
    if expiry <= now + 30 or expiry > now + 3 * 60 * 60:
        expiry = now + 55 * 60

    # Validate against the exact API endpoint Tasia uses for text search.
    try:
        response = httpx.get(
            "https://api.spotify.com/v1/search",
            params={"q": "music", "type": "track", "limit": 1},
            headers={"Authorization": f"Bearer {clean}", "Accept": "application/json"},
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise ValueError(f"Spotify token validation failed: {exc}") from exc
    if response.status_code == 401:
        raise ValueError("Spotify rejected the captured token (HTTP 401)")
    if response.status_code >= 400 and response.status_code != 429:
        raise ValueError(f"Spotify token validation returned HTTP {response.status_code}")

    _atomic_json(
        _token_path(user_id),
        {"access_token": clean, "expires_at": expiry, "updated_at": now, "reason": str(reason or "captured")[:120]},
    )
    return _token_state(user_id)


def _invalidate_token(user_id: int) -> None:
    path = _token_path(user_id)
    if not path.exists():
        return
    data = _read_json(path)
    data["expires_at"] = 0
    data["invalidated_at"] = time.time()
    _atomic_json(path, data)


def _cache_load(user_id: int) -> dict:
    payload = _read_json(_cache_path(user_id), {"entries": {}})
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    return {"entries": entries}


def _cache_get(user_id: int, query: str, limit: int) -> list[dict] | None:
    key = " ".join(str(query or "").lower().split())
    if not key:
        return None
    payload = _cache_load(user_id)
    row = payload["entries"].get(key)
    if not isinstance(row, dict):
        return None
    created = float(row.get("created_at") or 0)
    if created <= time.time() - SEARCH_CACHE_SECONDS:
        return None
    rows = row.get("rows")
    if not isinstance(rows, list):
        return None
    return [x for x in rows if isinstance(x, dict)][: max(1, min(int(limit), 50))]


def _cache_put(user_id: int, query: str, rows: list[dict]) -> None:
    key = " ".join(str(query or "").lower().split())
    if not key:
        return
    payload = _cache_load(user_id)
    entries = payload["entries"]
    now = time.time()
    # Drop expired entries before enforcing the cap.
    entries = {
        k: v for k, v in entries.items()
        if isinstance(v, dict) and float(v.get("created_at") or 0) > now - SEARCH_CACHE_SECONDS
    }
    entries[key] = {"created_at": now, "rows": rows[:50]}
    if len(entries) > MAX_CACHE_ENTRIES:
        ordered = sorted(entries.items(), key=lambda kv: float((kv[1] or {}).get("created_at") or 0), reverse=True)
        entries = dict(ordered[:MAX_CACHE_ENTRIES])
    _atomic_json(_cache_path(user_id), {"entries": entries})


def _spotify_track(row: dict) -> dict:
    artists = row.get("artists") or []
    artist = ", ".join(str(x.get("name") or "") for x in artists if isinstance(x, dict) and x.get("name"))
    album = row.get("album") or {}
    images = album.get("images") or []
    artwork = str(images[0].get("url") or "") if images and isinstance(images[0], dict) else ""
    url = str((row.get("external_urls") or {}).get("spotify") or "")
    tid = str(row.get("id") or "")
    if not url and tid:
        url = f"https://open.spotify.com/track/{tid}"
    return {
        "provider": SPOTIFY_PROVIDER,
        "id": btch.pack_url(url),
        "title": str(row.get("name") or "Untitled"),
        "artist": artist,
        "duration": (float(row.get("duration_ms") or 0) / 1000.0) or None,
        "url": url,
        "artwork": artwork,
        "license": "Spotify catalog / BTCH resolver",
        "access": "playable",
    }


def _spotify_search_with_bridge(settings: dict, query: str, limit: int) -> list[dict]:
    user_id = int(settings.get("user_id") or 0)
    if user_id <= 0:
        raise catalogs.CatalogError("Spotify search is missing the Tasia user context")

    cached = _cache_get(user_id, query, limit)
    if cached is not None:
        return cached

    state = _token_state(user_id)
    if not state["connected"]:
        raise catalogs.CatalogError(
            "Spotify text search needs a fresh browser token. Open the Tasia Spotify Connector and press Refresh token now. "
            "Spotify track URLs still work through BTCH without this token."
        )

    data = _read_json(_token_path(user_id))
    token = str(data.get("access_token") or "").strip()
    try:
        response = httpx.get(
            "https://api.spotify.com/v1/search",
            params={"q": query, "type": "track", "limit": max(1, min(int(limit), 50))},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise catalogs.CatalogError(f"Spotify search connection failed: {exc}") from exc

    if response.status_code == 401:
        _invalidate_token(user_id)
        raise catalogs.CatalogError(
            "Spotify search token expired. Tasia Spotify Connector will refresh it when Spotify web player is available; try again in a moment."
        )
    if response.status_code == 429:
        raise catalogs.CatalogError("Spotify rate limit reached (HTTP 429). Cached searches remain available for 7 days.")
    if response.status_code >= 400:
        raise catalogs.CatalogError(f"Spotify search returned HTTP {response.status_code}: {response.text[:240]}")

    payload = response.json() or {}
    items = ((payload.get("tracks") or {}).get("items") or []) if isinstance(payload, dict) else []
    rows = [_spotify_track(x) for x in items if isinstance(x, dict) and x.get("id")]
    _cache_put(user_id, query, rows)
    return rows[: max(1, min(int(limit), 50))]


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


_original_search = catalogs.search
_original_test = catalogs.test


def _search_wrapper(provider: str, settings: dict, query: str, limit: int = 30) -> list[dict]:
    provider = str(provider or "").lower()
    if provider != SPOTIFY_PROVIDER or _looks_like_url(query):
        return _original_search(provider, settings, query, limit)
    return _spotify_search_with_bridge(settings, str(query or "").strip(), limit)


def _test_wrapper(provider: str, settings: dict) -> dict:
    provider = str(provider or "").lower()
    if provider != SPOTIFY_PROVIDER:
        return _original_test(provider, settings)
    btch_status = _original_test(provider, settings)
    user_id = int(settings.get("user_id") or 0)
    token = _token_state(user_id) if user_id > 0 else {"connected": False, "needs_refresh": True, "valid_for_seconds": 0}
    return {
        "ok": True,
        "message": "Spotify BTCH URL resolver ready. " + (
            f"Text-search token ready for {max(1, token['valid_for_seconds'] // 60)} min; search results cache for 7 days."
            if token.get("connected") else
            "Text search needs Tasia Spotify Connector; pasted Spotify URLs still work without it."
        ),
        "btch": btch_status,
        "spotify_token": token,
    }


router = APIRouter()


@router.post("/api/spotify/connector/session")
def spotify_connector_session(body: SpotifyConnectorIn):
    user = _find_user_by_connector_key(body.connector_key)
    if not user:
        raise HTTPException(401, "Invalid Tasia connector key")
    try:
        state = _save_token(int(user["id"]), body.token, body.expires_at, body.reason)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "username": user["username"], **state}


@router.post("/api/spotify/connector/status")
def spotify_connector_status(body: SpotifyConnectorStatusIn):
    user = _find_user_by_connector_key(body.connector_key)
    if not user:
        raise HTTPException(401, "Invalid Tasia connector key")
    user_id = int(user["id"])
    cache = _cache_load(user_id).get("entries") or {}
    return {"ok": True, "username": user["username"], "cache_entries": len(cache), **_token_state(user_id)}


@router.get("/api/spotify/connector/download")
def spotify_connector_download():
    source = Path(__file__).resolve().parent.parent / "extras" / "tasia-spotify-connector"
    if not source.is_dir():
        raise HTTPException(404, "Tasia Spotify Connector sources are missing from this build")
    handle = tempfile.NamedTemporaryFile(prefix="tasia-spotify-connector-", suffix=".zip", delete=False)
    archive = Path(handle.name)
    handle.close()
    try:
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    arcname = Path("tasia-spotify-connector") / path.relative_to(source)
                    zf.write(path, arcname.as_posix())
    except Exception:
        archive.unlink(missing_ok=True)
        raise
    return FileResponse(
        archive,
        media_type="application/zip",
        filename="tasia-spotify-connector.zip",
        background=BackgroundTask(archive.unlink, missing_ok=True),
    )


def install_spotify_bridge() -> None:
    if getattr(catalogs, "_tasia_spotify_bridge_installed", False):
        return
    catalogs.search = _search_wrapper
    catalogs.test = _test_wrapper
    catalogs._tasia_spotify_bridge_installed = True

    # app/__init__.py runs before app.main constructs FastAPI. Hook the constructor
    # once so the connector routes are registered without duplicating the huge
    # main.py route table or changing its existing beta29 behaviour.
    if getattr(FastAPI, "_tasia_spotify_bridge_installed", False):
        return
    original_init = FastAPI.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.include_router(router)

    FastAPI.__init__ = patched_init
    FastAPI._tasia_spotify_bridge_installed = True
