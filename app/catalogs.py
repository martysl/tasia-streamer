from __future__ import annotations

import base64
import json
import re
import time
from urllib.parse import quote, urlencode, urljoin, urlparse, unquote

import httpx

from .media import _assert_public_http_url
from . import universal, btch


class CatalogError(ValueError):
    pass


PROVIDERS = ("universal", "soundcloud", "audius", "jamendo", "stremio", "btch-spotify", "btch-soundcloud", "btch-gdrive")
_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_MANIFEST_CACHE: dict[str, tuple[dict, float]] = {}
_SPOTIFY_ANON_TOKEN: tuple[str, float] | None = None


def _raise_for(resp: httpx.Response, provider: str) -> None:
    if resp.status_code < 400:
        return
    detail = ""
    try:
        data = resp.json()
        if isinstance(data, dict):
            detail = str(data.get("error_description") or data.get("message") or data.get("errors") or data.get("error") or "")
    except Exception:
        detail = resp.text[:300]
    if resp.status_code == 401:
        raise CatalogError(f"{provider} authentication failed (HTTP 401). Check the saved API credentials.")
    if resp.status_code == 429:
        raise CatalogError(f"{provider} rate limit reached (HTTP 429). Try again after the provider resets the limit.")
    raise CatalogError(f"{provider} returned HTTP {resp.status_code}: {detail or resp.reason_phrase}")


# Spotify public catalog search -------------------------------------------------
def _spotify_anon_token() -> str:
    global _SPOTIFY_ANON_TOKEN
    if _SPOTIFY_ANON_TOKEN and _SPOTIFY_ANON_TOKEN[1] > time.time() + 60:
        return _SPOTIFY_ANON_TOKEN[0]
    headers={"Accept":"text/html,application/json;q=0.9,*/*;q=0.8","User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36"}
    data={}
    # Spotify has exposed the anonymous web-player session in two shapes over
    # time. Try the compact token endpoint first, then the session JSON embedded
    # in the public /search page.
    try:
        r=httpx.get("https://open.spotify.com/get_access_token",params={"reason":"transport","productType":"web_player"},headers=headers,timeout=15)
        if r.status_code < 400:
            data=r.json() or {}
    except Exception:
        data={}
    if not data.get("accessToken"):
        try:
            r=httpx.get("https://open.spotify.com/search",headers=headers,follow_redirects=True,timeout=20)
            _raise_for(r,"Spotify")
            m=re.search(r'<script[^>]+id=["\\\']session["\\\'][^>]*>(\{.*?\})</script>',r.text,re.I|re.S)
            if not m:
                m=re.search(r'<script[^>]+data-testid=["\\\']session["\\\'][^>]*>(\{.*?\})</script>',r.text,re.I|re.S)
            if m:
                data=json.loads(m.group(1))
        except (httpx.HTTPError,ValueError,json.JSONDecodeError) as exc:
            raise CatalogError(f"Spotify public search token failed: {exc}") from exc
    token=str(data.get("accessToken") or "").strip()
    if not token:
        raise CatalogError("Spotify public web player did not return an anonymous search token")
    expires=float(data.get("accessTokenExpirationTimestampMs") or 0)/1000.0
    if expires <= time.time(): expires=time.time()+1800
    _SPOTIFY_ANON_TOKEN=(token,expires)
    return token


def _spotify_track(row:dict)->dict:
    artists=row.get("artists") or []
    artist=", ".join(str(x.get("name") or "") for x in artists if isinstance(x,dict) and x.get("name"))
    album=row.get("album") or {}
    images=album.get("images") or []
    artwork=str(images[0].get("url") or "") if images and isinstance(images[0],dict) else ""
    url=str((row.get("external_urls") or {}).get("spotify") or "")
    tid=str(row.get("id") or "")
    if not url and tid: url=f"https://open.spotify.com/track/{tid}"
    return {
        "provider":"btch-spotify",
        "id":btch.pack_url(url),
        "title":str(row.get("name") or "Untitled"),
        "artist":artist,
        "duration":(float(row.get("duration_ms") or 0)/1000.0) or None,
        "url":url,
        "artwork":artwork,
        "license":"Spotify catalog / BTCH resolver",
        "access":"playable",
    }


def _spotify_search(query:str,limit:int)->list[dict]:
    token=_spotify_anon_token()
    try:
        r=httpx.get("https://api.spotify.com/v1/search",params={"q":query,"type":"track","limit":max(1,min(int(limit),50))},headers={"Authorization":f"Bearer {token}","Accept":"application/json"},timeout=20)
        if r.status_code==401:
            global _SPOTIFY_ANON_TOKEN
            _SPOTIFY_ANON_TOKEN=None
            token=_spotify_anon_token()
            r=httpx.get("https://api.spotify.com/v1/search",params={"q":query,"type":"track","limit":max(1,min(int(limit),50))},headers={"Authorization":f"Bearer {token}","Accept":"application/json"},timeout=20)
        _raise_for(r,"Spotify")
        items=((r.json() or {}).get("tracks") or {}).get("items") or []
        return [_spotify_track(x) for x in items if isinstance(x,dict) and x.get("id")]
    except httpx.HTTPError as exc:
        raise CatalogError(f"Spotify public search failed: {exc}") from exc


# SoundCloud -------------------------------------------------------------------
def _soundcloud_token(settings: dict) -> str:
    cid = str(settings.get("client_id") or "").strip()
    secret = str(settings.get("client_secret") or "").strip()
    if not cid or not secret:
        raise CatalogError("SoundCloud needs a Client ID and Client Secret in Settings → Streaming Catalogs.")
    key = (cid, secret)
    cached = _TOKEN_CACHE.get(key)
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    try:
        r = httpx.post(
            "https://secure.soundcloud.com/oauth/token",
            auth=httpx.BasicAuth(cid, secret),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise CatalogError(f"SoundCloud token connection failed: {exc}") from exc
    _raise_for(r, "SoundCloud")
    data = r.json()
    token = str(data.get("access_token") or "")
    if not token:
        raise CatalogError("SoundCloud did not return an access token.")
    expires = max(120, int(data.get("expires_in") or 3600))
    _TOKEN_CACHE[key] = (token, time.time() + expires)
    return token


def _sc_headers(settings: dict) -> dict[str, str]:
    return {"Authorization": f"OAuth {_soundcloud_token(settings)}", "Accept": "application/json"}


def _sc_track(row: dict) -> dict:
    urn = str(row.get("urn") or (f"soundcloud:tracks:{row.get('id')}" if row.get("id") is not None else ""))
    user = row.get("user") or {}
    return {
        "provider": "soundcloud",
        "id": urn,
        "title": str(row.get("title") or "Untitled"),
        "artist": str(user.get("username") or user.get("full_name") or ""),
        "duration": (float(row.get("duration") or 0) / 1000.0) or None,
        "url": str(row.get("permalink_url") or ""),
        "artwork": str(row.get("artwork_url") or ""),
        "license": str(row.get("license") or ""),
        "access": str(row.get("access") or "playable"),
    }


# Audius -----------------------------------------------------------------------
def _audius_headers(settings: dict) -> dict[str, str]:
    token = str(settings.get("bearer_token") or "").strip()
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"} if token else {"Accept": "application/json"}


def _audius_track(row: dict) -> dict:
    user = row.get("user") or {}
    artwork = row.get("artwork") or {}
    return {
        "provider": "audius",
        "id": str(row.get("id") or ""),
        "title": str(row.get("title") or "Untitled"),
        "artist": str(user.get("name") or user.get("handle") or ""),
        "duration": float(row.get("duration") or 0) or None,
        "url": str(row.get("permalink") or ""),
        "artwork": str(artwork.get("480x480") or artwork.get("_480x480") or artwork.get("150x150") or ""),
        "license": "Audius / Open Audio Protocol",
        "access": "playable" if row.get("is_streamable", row.get("isStreamable", True)) not in (False, "false") else "blocked",
    }


# Jamendo ----------------------------------------------------------------------
def _jamendo_track(row: dict) -> dict:
    return {
        "provider": "jamendo",
        "id": str(row.get("id") or ""),
        "title": str(row.get("name") or "Untitled"),
        "artist": str(row.get("artist_name") or ""),
        "duration": float(row.get("duration") or 0) or None,
        "url": str(row.get("shareurl") or row.get("shorturl") or ""),
        "artwork": str(row.get("image") or row.get("album_image") or ""),
        "license": str(row.get("license_ccurl") or ""),
        "access": "playable" if row.get("audio") else "blocked",
    }


# Stremio Addon Protocol -------------------------------------------------------
# This is deliberately a narrow integration. It consumes a user-configured addon
# manifest and accepts only direct http(s) `stream.url` responses. It does NOT
# resolve torrents/infoHash, yt_id, external-player URLs, magnet links or DRM.

def _stremio_manifest_url(settings: dict) -> str:
    raw = str(settings.get("base_url") or "").strip()
    if not raw:
        raise CatalogError("Stremio needs an addon manifest URL in Settings → Streaming Catalogs.")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise CatalogError("Stremio addon URL must use http:// or https://")
    if not parsed.path.endswith(".json"):
        raw = raw.rstrip("/") + "/manifest.json"
    try:
        _assert_public_http_url(raw)
    except ValueError as exc:
        raise CatalogError(str(exc)) from exc
    return raw


def _stremio_manifest(settings: dict, *, refresh: bool = False) -> tuple[str, dict]:
    url = _stremio_manifest_url(settings)
    cached = _MANIFEST_CACHE.get(url)
    if not refresh and cached and cached[1] > time.time():
        return url, cached[0]
    try:
        r = httpx.get(url, follow_redirects=True, headers={"Accept": "application/json", "User-Agent": "Tasia-Streamer/2.0"}, timeout=20)
    except httpx.HTTPError as exc:
        raise CatalogError(f"Stremio addon manifest connection failed: {exc}") from exc
    _raise_for(r, "Stremio addon")
    try:
        data = r.json()
    except Exception as exc:
        raise CatalogError("Stremio addon did not return a JSON manifest.") from exc
    if not isinstance(data, dict) or not data.get("id") or not isinstance(data.get("catalogs", []), list):
        raise CatalogError("The URL did not return a valid Stremio addon manifest.")
    _MANIFEST_CACHE[url] = (data, time.time() + 300)
    return url, data


def _stremio_resource(manifest: dict, name: str) -> bool:
    for resource in manifest.get("resources") or []:
        if resource == name:
            return True
        if isinstance(resource, dict) and resource.get("name") == name:
            return True
    return False


def _stremio_base(manifest_url: str) -> str:
    return manifest_url.rsplit("/", 1)[0].rstrip("/") + "/"


def _stremio_catalog_supports_search(catalog: dict) -> bool:
    return any(isinstance(x, dict) and x.get("name") == "search" for x in (catalog.get("extra") or []))


def _stremio_catalog_requires_search(catalog: dict) -> bool:
    return any(isinstance(x, dict) and x.get("name") == "search" and bool(x.get("isRequired")) for x in (catalog.get("extra") or []))


def _stremio_video_id(meta: dict) -> str:
    hints = meta.get("behaviorHints") or {}
    if isinstance(hints, dict) and hints.get("defaultVideoId"):
        return str(hints["defaultVideoId"])
    videos = meta.get("videos") or []
    if isinstance(videos, list):
        for video in videos:
            if isinstance(video, dict) and video.get("id"):
                return str(video["id"])
    return str(meta.get("id") or "")


def _pack_stremio_id(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unpack_stremio_id(value: str) -> dict:
    try:
        padded = value + "=" * ((4 - len(value) % 4) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise CatalogError("Invalid Stremio catalog item id.") from exc
    if not isinstance(data, dict) or not data.get("type") or not data.get("video_id"):
        raise CatalogError("Invalid Stremio catalog item id.")
    return data


def _stremio_streams(settings: dict, content_type: str, video_id: str) -> list[dict]:
    manifest_url, manifest = _stremio_manifest(settings)
    if not _stremio_resource(manifest, "stream"):
        raise CatalogError("This Stremio addon does not expose the stream resource.")
    base = _stremio_base(manifest_url)
    path = f"stream/{quote(content_type, safe='')}/{quote(video_id, safe='')}.json"
    url = urljoin(base, path)
    try:
        _assert_public_http_url(url)
        r = httpx.get(url, follow_redirects=True, headers={"Accept": "application/json", "User-Agent": "Tasia-Streamer/2.0"}, timeout=20)
    except (httpx.HTTPError, ValueError) as exc:
        raise CatalogError(f"Stremio stream lookup failed: {exc}") from exc
    _raise_for(r, "Stremio addon")
    payload = r.json() or {}
    rows = payload.get("streams", []) if isinstance(payload, dict) else []
    return [x for x in rows if isinstance(x, dict)]


def _stremio_direct_url(settings: dict, packed_id: str) -> str:
    info = _unpack_stremio_id(packed_id)
    streams = _stremio_streams(settings, str(info["type"]), str(info["video_id"]))
    rejected = 0
    for stream in streams:
        url = str(stream.get("url") or "").strip()
        if not url:
            rejected += 1
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            rejected += 1
            continue
        try:
            _assert_public_http_url(url)
        except ValueError:
            rejected += 1
            continue
        return url
    if rejected:
        raise CatalogError("Stremio item has streams, but none are direct allowed HTTP(S) URLs. Torrent/YouTube/external-player/DRM-style streams are intentionally unsupported.")
    raise CatalogError("Stremio addon returned no playable direct HTTP(S) stream for this item.")


def _runtime_seconds(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if float(value) > 0 else None
    text = str(value).lower().strip()
    if not text:
        return None
    # Common Stremio runtime strings: "4 min", "1h 32min", "01:32:00".
    if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
        parts = [int(x) for x in text.split(":")]
        return float(parts[0] * 60 + parts[1]) if len(parts) == 2 else float(parts[0] * 3600 + parts[1] * 60 + parts[2])
    h = re.search(r"(\d+(?:\.\d+)?)\s*h", text)
    m = re.search(r"(\d+(?:\.\d+)?)\s*m", text)
    s = re.search(r"(\d+(?:\.\d+)?)\s*s", text)
    total = (float(h.group(1)) * 3600 if h else 0) + (float(m.group(1)) * 60 if m else 0) + (float(s.group(1)) if s else 0)
    return total or None


def _stremio_search(settings: dict, query: str, limit: int) -> list[dict]:
    manifest_url, manifest = _stremio_manifest(settings)
    if not _stremio_resource(manifest, "catalog"):
        raise CatalogError("This Stremio addon does not expose catalogs.")
    if not _stremio_resource(manifest, "stream"):
        raise CatalogError("This Stremio addon has catalogs but no stream resource.")
    base = _stremio_base(manifest_url)
    addon_name = str(manifest.get("name") or manifest.get("id") or "Stremio addon")
    catalogs = [x for x in (manifest.get("catalogs") or []) if isinstance(x, dict) and x.get("type") and x.get("id")]
    rows: list[dict] = []
    for catalog in catalogs:
        if len(rows) >= limit:
            break
        if query and not _stremio_catalog_supports_search(catalog):
            continue
        if not query and _stremio_catalog_requires_search(catalog):
            continue
        ctype, cid = str(catalog["type"]), str(catalog["id"])
        path = f"catalog/{quote(ctype, safe='')}/{quote(cid, safe='')}"
        if query:
            path += "/" + urlencode({"search": query}, quote_via=quote)
        url = urljoin(base, path + ".json")
        try:
            _assert_public_http_url(url)
            r = httpx.get(url, follow_redirects=True, headers={"Accept": "application/json", "User-Agent": "Tasia-Streamer/2.0"}, timeout=20)
            if r.status_code == 404:
                continue
            _raise_for(r, "Stremio addon")
            payload = r.json() or {}
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            continue
        metas = payload.get("metas", []) if isinstance(payload, dict) else []
        for meta in metas:
            if len(rows) >= limit:
                break
            if not isinstance(meta, dict) or not meta.get("id"):
                continue
            video_id = _stremio_video_id(meta)
            if not video_id:
                continue
            packed = _pack_stremio_id({
                "type": str(meta.get("type") or ctype),
                "video_id": video_id,
                "meta_id": str(meta.get("id")),
                "title": str(meta.get("name") or "Untitled"),
                "catalog": str(catalog.get("name") or cid),
                "addon": addon_name,
                "duration": _runtime_seconds(meta.get("runtime")),
            })
            # Only display items that really resolve to a supported direct stream.
            try:
                _stremio_direct_url(settings, packed)
            except CatalogError:
                continue
            rows.append({
                "provider": "stremio",
                "id": packed,
                "title": str(meta.get("name") or "Untitled"),
                "artist": str(catalog.get("name") or addon_name),
                "duration": _runtime_seconds(meta.get("runtime")),
                "url": manifest_url,
                "artwork": str(meta.get("poster") or meta.get("logo") or ""),
                "license": f"Stremio addon: {addon_name}",
                "access": "playable",
            })
    return rows


# Public provider API ----------------------------------------------------------
def search(provider: str, settings: dict, query: str, limit: int = 30) -> list[dict]:
    provider = provider.lower()
    query = query.strip()
    limit = max(1, min(int(limit), 50))
    if provider != "stremio" and not query:
        return []
    try:
        if provider == "universal":
            return universal.search(query, limit, int(settings.get("user_id") or 0))
        if provider == "soundcloud":
            r = httpx.get("https://api.soundcloud.com/tracks", headers=_sc_headers(settings), params={"q": query, "access": "playable", "limit": limit, "linked_partitioning": "true"}, timeout=25)
            _raise_for(r, "SoundCloud")
            payload = r.json()
            rows = payload.get("collection", []) if isinstance(payload, dict) else payload
            return [_sc_track(x) for x in rows if isinstance(x, dict) and x.get("access") != "blocked"]
        if provider == "audius":
            r = httpx.get("https://api.audius.co/v1/tracks/search", headers=_audius_headers(settings), params={"query": query, "limit": limit}, timeout=25)
            _raise_for(r, "Audius")
            rows = (r.json() or {}).get("data", [])
            return [_audius_track(x) for x in rows if isinstance(x, dict) and x.get("id")]
        if provider == "jamendo":
            cid = str(settings.get("client_id") or "").strip()
            if not cid:
                raise CatalogError("Jamendo needs a Client ID in Settings → Streaming Catalogs.")
            r = httpx.get("https://api.jamendo.com/v3.0/tracks/", params={"client_id": cid, "format": "json", "limit": limit, "search": query, "audioformat": "mp32", "include": "licenses"}, timeout=25)
            _raise_for(r, "Jamendo")
            payload = r.json()
            hdr = payload.get("headers") or {}
            if hdr.get("status") == "failed":
                raise CatalogError(f"Jamendo API error: {hdr.get('error_message') or hdr.get('code')}")
            return [_jamendo_track(x) for x in payload.get("results", []) if isinstance(x, dict) and x.get("audio")]
        if provider == "stremio":
            return _stremio_search(settings, query, limit)
        if provider == "btch-spotify":
            parsed=urlparse(query)
            if parsed.scheme in {"http","https"} and parsed.netloc:
                return [btch.resolve(provider, query)]
            return _spotify_search(query, limit)
        if provider in {"btch-soundcloud","btch-gdrive"}:
            parsed=urlparse(query)
            if parsed.scheme not in {"http","https"} or not parsed.netloc:
                label="SoundCloud" if provider=="btch-soundcloud" else "Google Drive"
                raise CatalogError(f"{label} BTCH resolver needs a URL. Use SoundCloud Search or All Sources for song names.")
            return [btch.resolve(provider, query)]
    except httpx.HTTPError as exc:
        raise CatalogError(f"{provider} connection failed: {exc}") from exc
    raise CatalogError("Unsupported catalog provider")


def get_track(provider: str, settings: dict, track_id: str) -> dict:
    provider = provider.lower()
    tid = str(track_id).strip()
    if not tid:
        raise CatalogError("Missing catalog track id")
    try:
        if provider == "universal":
            return universal.get_track(tid)
        if provider == "soundcloud":
            r = httpx.get(f"https://api.soundcloud.com/tracks/{quote(tid, safe=':')}", headers=_sc_headers(settings), timeout=20)
            _raise_for(r, "SoundCloud")
            return _sc_track(r.json())
        if provider == "audius":
            r = httpx.get(f"https://api.audius.co/v1/tracks/{quote(tid, safe='')}", headers=_audius_headers(settings), timeout=20)
            _raise_for(r, "Audius")
            return _audius_track((r.json() or {}).get("data") or {})
        if provider == "jamendo":
            cid = str(settings.get("client_id") or "").strip()
            if not cid:
                raise CatalogError("Jamendo needs a Client ID in Settings → Streaming Catalogs.")
            r = httpx.get("https://api.jamendo.com/v3.0/tracks/", params={"client_id": cid, "format": "json", "id": tid, "audioformat": "mp32", "include": "licenses"}, timeout=20)
            _raise_for(r, "Jamendo")
            rows = (r.json() or {}).get("results", [])
            if not rows:
                raise CatalogError("Jamendo track was not found")
            return _jamendo_track(rows[0])
        if provider == "stremio":
            info = _unpack_stremio_id(tid)
            # Resolve now so Q/P cannot add a dead or unsupported transport.
            _stremio_direct_url(settings, tid)
            manifest_url, _ = _stremio_manifest(settings)
            return {
                "provider": "stremio",
                "id": tid,
                "title": str(info.get("title") or "Untitled"),
                "artist": str(info.get("catalog") or info.get("addon") or "Stremio addon"),
                "duration": float(info.get("duration")) if info.get("duration") not in (None, "") else None,
                "url": manifest_url,
                "artwork": "",
                "license": f"Stremio addon: {info.get('addon') or ''}".rstrip(),
                "access": "playable",
            }
        if provider in btch.PROVIDERS:
            return btch.resolve(provider, btch.unpack_url(tid))
    except httpx.HTTPError as exc:
        raise CatalogError(f"{provider} connection failed: {exc}") from exc
    raise CatalogError("Unsupported catalog provider")


def stream_url(provider: str, settings: dict, track_id: str) -> str:
    provider = provider.lower()
    tid = str(track_id).strip()
    try:
        if provider == "soundcloud":
            # SoundCloud's current streams endpoint returns the modern AAC/HLS
            # playback URLs. Prefer 160 kbps and fall back to 96 kbps.
            r = httpx.get(f"https://api.soundcloud.com/tracks/{quote(tid, safe=':')}/streams", headers=_sc_headers(settings), timeout=20)
            _raise_for(r, "SoundCloud")
            data = r.json() or {}
            url = data.get("hls_aac_160_url") or data.get("hls_aac_96_url")
            if not url:
                raise CatalogError("SoundCloud did not provide a playable AAC/HLS stream for this track.")
            return str(url)
        if provider == "audius":
            client = httpx.Client(headers=_audius_headers(settings), follow_redirects=True, timeout=25)
            req = client.build_request("GET", f"https://api.audius.co/v1/tracks/{quote(tid, safe='')}/stream")
            resp = client.send(req, stream=True)
            try:
                _raise_for(resp, "Audius")
                final = str(resp.url)
                if not final:
                    raise CatalogError("Audius did not return a playable stream URL.")
                return final
            finally:
                resp.close(); client.close()
        if provider == "jamendo":
            cid = str(settings.get("client_id") or "").strip()
            if not cid:
                raise CatalogError("Jamendo needs a Client ID in Settings → Streaming Catalogs.")
            r = httpx.get("https://api.jamendo.com/v3.0/tracks/", params={"client_id": cid, "format": "json", "id": tid, "audioformat": "mp32"}, timeout=20)
            _raise_for(r, "Jamendo")
            rows = (r.json() or {}).get("results", [])
            if not rows or not rows[0].get("audio"):
                raise CatalogError("Jamendo did not return a playable stream for this track.")
            return str(rows[0]["audio"])
        if provider == "stremio":
            return _stremio_direct_url(settings, tid)
        if provider in btch.PROVIDERS:
            return str(btch.resolve(provider, btch.unpack_url(tid))["media_url"])
    except httpx.HTTPError as exc:
        raise CatalogError(f"{provider} stream resolution failed: {exc}") from exc
    raise CatalogError("Unsupported catalog provider")


def test(provider: str, settings: dict) -> dict:
    provider = provider.lower()
    if provider == "universal":
        return universal.runtime_status(int(settings.get("user_id") or 0))
    if provider == "soundcloud":
        _soundcloud_token(settings)
        return {"ok": True, "message": "SoundCloud credentials accepted."}
    if provider == "audius":
        rows = search("audius", settings, "music", 1)
        return {"ok": True, "message": f"Audius catalog reachable ({len(rows)} test result)."}
    if provider == "jamendo":
        rows = search("jamendo", settings, "music", 1)
        return {"ok": True, "message": f"Jamendo client ID accepted ({len(rows)} test result)."}
    if provider == "stremio":
        manifest_url, manifest = _stremio_manifest(settings, refresh=True)
        catalogs = [x for x in (manifest.get("catalogs") or []) if isinstance(x, dict)]
        if not _stremio_resource(manifest, "catalog") or not _stremio_resource(manifest, "stream"):
            raise CatalogError("Stremio addon must expose both catalog and stream resources.")
        return {"ok": True, "message": f"Stremio addon '{manifest.get('name') or manifest.get('id')}' loaded with {len(catalogs)} catalog(s).", "manifest_url": manifest_url}
    if provider in btch.PROVIDERS:
        return btch.runtime_status()
    raise CatalogError("Unsupported catalog provider")


def make_path(provider: str, track_id: str) -> str:
    return f"catalog:{provider.lower()}:{quote(str(track_id), safe='')}"


def parse_path(value: str) -> tuple[str, str] | None:
    raw = str(value or "")
    if not raw.startswith("catalog:"):
        return None
    parts = raw.split(":", 2)
    if len(parts) != 3 or parts[1] not in PROVIDERS:
        return None
    return parts[1], unquote(parts[2])
