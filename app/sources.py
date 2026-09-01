from __future__ import annotations

import ftplib
import hashlib
import os
import posixpath
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit, urlunsplit

import httpx

from .config import AUDIO_EXTENSIONS, MAX_REMOTE_BYTES, USER_DATA_DIR
from .media import ffprobe, read_tags


class RemoteAuthError(ValueError):
    pass


class RemoteSourceError(ValueError):
    pass


def _audio_name(name: str) -> bool:
    return Path(name).suffix.lower() in AUDIO_EXTENSIONS


def _source_credentials(source: dict) -> tuple[str, str]:
    """Use explicit fields first, then credentials embedded in the URL."""
    user = str(source.get("username") or "")
    password = str(source.get("password") or "")
    try:
        p = urlsplit(str(source.get("url") or ""))
        if not user and p.username:
            user = unquote(p.username)
        if not password and p.password:
            password = unquote(p.password)
    except Exception:
        pass
    return user, password


def _clean_http_url(raw: str, label: str = "WebDAV") -> str:
    """Strip userinfo from a URL so it is not leaked in errors/logs."""
    p = urlsplit(raw.strip())
    if p.scheme not in {"http", "https"}:
        raise RemoteSourceError(f"{label} URL must start with http:// or https://")
    host = p.hostname or ""
    if not host:
        raise RemoteSourceError(f"{label} URL is missing a hostname")
    netloc = host
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]"
    if p.port:
        netloc += f":{p.port}"
    return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))


def _webdav_url(base: str, path: str) -> str:
    base = _clean_http_url(base).rstrip("/") + "/"
    # Quote individual path components but keep slashes. WebDAV servers expect
    # URL encoding on the wire; returned hrefs are decoded again for the UI.
    clean = "/".join(quote(unquote(x), safe="") for x in path.strip("/").split("/") if x)
    return urljoin(base, clean)


def _webdav_request(source: dict, method: str, url: str, **kwargs) -> httpx.Response:
    """
    WebDAV auth helper.

    Most DAV servers use Basic auth, while Apache/IIS installations often use
    Digest. httpx does not auto-negotiate between them, so try Basic first and
    transparently retry Digest when the 401 challenge advertises it.
    """
    username, password = _source_credentials(source)
    timeout = kwargs.pop("timeout", httpx.Timeout(30.0, connect=12.0))
    common = dict(follow_redirects=True, timeout=timeout, **kwargs)

    auth = httpx.BasicAuth(username, password) if username else None
    response = httpx.request(method, url, auth=auth, **common)
    if response.status_code == 401 and username:
        challenge = response.headers.get("www-authenticate", "")
        response.close()
        if "digest" in challenge.lower():
            response = httpx.request(method, url, auth=httpx.DigestAuth(username, password), **common)

    if response.status_code == 401:
        challenge = response.headers.get("www-authenticate", "").strip()
        offered = challenge.split(" ", 1)[0] if challenge else "unknown"
        response.close()
        raise RemoteAuthError(
            f"WebDAV authentication failed (HTTP 401, server auth: {offered}). "
            "Check the WebDAV username/password or use an app password if your server requires one."
        )
    return response


def browse_webdav(source: dict, path: str = "", query: str = "") -> list[dict]:
    joined = posixpath.join(source.get("root_path", "").strip("/"), path.strip("/")).strip("/")
    url = _webdav_url(source["url"], joined)
    headers = {"Depth": "1", "Content-Type": "application/xml; charset=utf-8"}
    body = """<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:displayname/><d:resourcetype/><d:getcontentlength/></d:prop></d:propfind>"""
    r = _webdav_request(source, "PROPFIND", url, headers=headers, content=body)
    try:
        if r.status_code not in {207, 200}:
            raise RemoteSourceError(f"WebDAV returned HTTP {r.status_code}: {r.reason_phrase}")
        root = ET.fromstring(r.content)
    except ET.ParseError as exc:
        raise RemoteSourceError("WebDAV returned invalid XML instead of a directory listing") from exc
    finally:
        r.close()

    out: list[dict] = []
    requested = urlparse(url).path.rstrip("/") + "/"
    for resp in root.findall("{DAV:}response"):
        href = resp.findtext("{DAV:}href") or ""
        href_path = unquote(urlparse(href).path)
        if href_path.rstrip("/") == requested.rstrip("/"):
            continue
        prop = resp.find(".//{DAV:}prop")
        name = (prop.findtext("{DAV:}displayname") if prop is not None else "") or posixpath.basename(href_path.rstrip("/"))
        is_dir = prop is not None and prop.find(".//{DAV:}collection") is not None
        rel = posixpath.join(path.strip("/"), name).strip("/")
        if is_dir or _audio_name(name):
            out.append({"name": name, "path": rel, "is_dir": is_dir})
    if query.strip():
        q = query.strip().lower()
        out = [row for row in out if q in row["name"].lower()]
    return sorted(out, key=lambda x: (not x["is_dir"], x["name"].lower()))


def _ftp_connect(source: dict):
    raw = str(source.get("url") or "").strip()
    parsed = urlparse(raw if "://" in raw else "ftp://" + raw)
    if parsed.scheme not in {"ftp", "ftps"}:
        raise RemoteSourceError("FTP URL must start with ftp:// or ftps://")
    username, password = _source_credentials(source)
    if not username:
        username = "anonymous"
        password = password or "tasia-streamer@localhost"
    cls = ftplib.FTP_TLS if parsed.scheme == "ftps" else ftplib.FTP
    ftp = cls()
    try:
        ftp.connect(parsed.hostname or "", parsed.port or 21, timeout=20)
        ftp.login(username, password)
        if isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()
        ftp.set_pasv(True)
    except ftplib.error_perm as exc:
        try:
            ftp.close()
        except Exception:
            pass
        msg = str(exc)
        if msg.startswith("530"):
            raise RemoteAuthError(f"FTP authentication failed: {msg}") from exc
        raise RemoteSourceError(f"FTP server rejected the request: {msg}") from exc
    except Exception as exc:
        try:
            ftp.close()
        except Exception:
            pass
        raise RemoteSourceError(f"FTP connection failed: {exc}") from exc
    base = (parsed.path or "").strip("/")
    return ftp, base


def browse_ftp(source: dict, path: str = "", query: str = "") -> list[dict]:
    ftp, base = _ftp_connect(source)
    directory = "/".join(x for x in [base, source.get("root_path", "").strip("/"), path.strip("/")] if x)
    try:
        rows: list[dict] = []
        try:
            listing = ftp.mlsd(directory or ".")
            for name, facts in listing:
                if name in {".", ".."}:
                    continue
                is_dir = facts.get("type") == "dir"
                if is_dir or _audio_name(name):
                    rows.append({"name": name, "path": posixpath.join(path, name).strip("/"), "is_dir": is_dir})
        except (ftplib.error_perm, NotImplementedError):
            # Older FTP servers do not implement MLSD. Fall back to NLST and
            # probe entries to determine whether they are directories.
            original = ftp.pwd()
            if directory:
                ftp.cwd(directory)
            try:
                for raw_name in ftp.nlst():
                    name = posixpath.basename(raw_name.rstrip("/"))
                    if name in {".", "..", ""}:
                        continue
                    is_dir = False
                    try:
                        here = ftp.pwd()
                        ftp.cwd(name)
                        ftp.cwd(here)
                        is_dir = True
                    except ftplib.error_perm:
                        is_dir = False
                    if is_dir or _audio_name(name):
                        rows.append({"name": name, "path": posixpath.join(path, name).strip("/"), "is_dir": is_dir})
            finally:
                try:
                    ftp.cwd(original)
                except Exception:
                    pass
        if query.strip():
            q = query.strip().lower()
            rows = [row for row in rows if q in row["name"].lower()]
        return sorted(rows, key=lambda x: (not x["is_dir"], x["name"].lower()))
    except ftplib.error_perm as exc:
        raise RemoteSourceError(f"FTP browse failed: {exc}") from exc
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


# Jellyfin ---------------------------------------------------------------------
# Jellyfin's normal password-login flow returns an access token.  Tasia keeps
# the user's Jellyfin credentials in the existing per-user source row, but the
# token itself is only cached in this process and is never exposed to the web UI.
_JELLYFIN_VERSION = "2.0.0-beta21"
_JELLYFIN_SESSIONS: dict[str, tuple[str, str, str]] = {}
_JELLYFIN_LOCK = threading.Lock()


def _jellyfin_base(raw: str) -> str:
    return _clean_http_url(raw, "Jellyfin").rstrip("/")


def _jellyfin_cache_key(source: dict) -> str:
    username, password = _source_credentials(source)
    # Include only a one-way password digest in the in-memory cache key so a
    # changed credential cannot accidentally reuse a session created with an
    # older password. The password itself is never copied into the cache.
    secret = hashlib.sha256(password.encode()).hexdigest()
    return hashlib.sha256(f"{_jellyfin_base(source.get('url',''))}|{username}|{secret}".encode()).hexdigest()


def _jellyfin_authorization(token: str = "", source: dict | None = None) -> str:
    suffix = ""
    if source:
        suffix = hashlib.sha256(str(source.get("url") or "").encode()).hexdigest()[:12]
    device_id = f"tasia-streamer-{suffix or 'server'}"
    value = (
        f'MediaBrowser Client="Tasia Streamer", Device="Tasia Streamer", '
        f'DeviceId="{device_id}", Version="{_JELLYFIN_VERSION}"'
    )
    if token:
        value += f', Token="{token}"'
    return value


def _jellyfin_login(source: dict, force: bool = False) -> tuple[str, str, str]:
    base = _jellyfin_base(str(source.get("url") or ""))
    username, password = _source_credentials(source)
    if not username:
        raise RemoteAuthError("Jellyfin username is required")
    key = _jellyfin_cache_key(source)
    if not force:
        with _JELLYFIN_LOCK:
            cached = _JELLYFIN_SESSIONS.get(key)
        if cached:
            return cached

    headers = {
        "Authorization": _jellyfin_authorization(source=source),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    try:
        r = httpx.post(
            f"{base}/Users/AuthenticateByName",
            headers=headers,
            json={"Username": username, "Pw": password},
            follow_redirects=True,
            timeout=httpx.Timeout(25.0, connect=12.0),
        )
    except httpx.HTTPError as exc:
        raise RemoteSourceError(f"Jellyfin connection failed: {exc}") from exc
    try:
        if r.status_code in {401, 403}:
            raise RemoteAuthError("Jellyfin authentication failed. Check username and password.")
        if r.status_code >= 400:
            raise RemoteSourceError(f"Jellyfin login returned HTTP {r.status_code}: {r.reason_phrase}")
        try:
            payload = r.json()
        except ValueError as exc:
            raise RemoteSourceError("Jellyfin login returned invalid JSON") from exc
        token = str(payload.get("AccessToken") or "")
        user = payload.get("User") or {}
        user_id = str(user.get("Id") or "")
        user_name = str(user.get("Name") or username)
        if not token or not user_id:
            raise RemoteSourceError("Jellyfin login succeeded but did not return an access token/user id")
        session = (token, user_id, user_name)
        with _JELLYFIN_LOCK:
            _JELLYFIN_SESSIONS[key] = session
        return session
    finally:
        r.close()


def _jellyfin_request(source: dict, method: str, endpoint: str, *, stream: bool = False, **kwargs):
    """Authenticated Jellyfin request with one automatic re-login on HTTP 401."""
    base = _jellyfin_base(str(source.get("url") or ""))
    key = _jellyfin_cache_key(source)
    last = None
    for attempt in range(2):
        token, user_id, user_name = _jellyfin_login(source, force=attempt > 0)
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.update({
            "Authorization": _jellyfin_authorization(token, source),
            "X-Emby-Token": token,
            "Accept": headers.get("Accept", "application/json, application/octet-stream;q=0.9, */*;q=0.8"),
        })
        client = httpx.Client(follow_redirects=True, timeout=httpx.Timeout(90.0, connect=15.0))
        request = client.build_request(method, f"{base}{endpoint}", headers=headers, **kwargs)
        response = client.send(request, stream=stream)
        # Keep the client alive while a streaming response is consumed.
        response.extensions["tasia_client"] = client
        if response.status_code != 401:
            return response, user_id, user_name
        last = response
        response.close(); client.close()
        with _JELLYFIN_LOCK:
            _JELLYFIN_SESSIONS.pop(key, None)
    if last is not None:
        last.close()
    raise RemoteAuthError("Jellyfin session was rejected (HTTP 401). Recheck username/password.")


def _close_jellyfin_response(response: httpx.Response) -> None:
    try:
        response.close()
    finally:
        client = response.extensions.get("tasia_client")
        if client:
            try:
                client.close()
            except Exception:
                pass


def _jf_segment(item: dict) -> str:
    item_id = str(item.get("Id") or "").strip()
    name = quote(str(item.get("Name") or item_id), safe="")
    return f"{item_id}~{name}"


def _jf_item_id(path: str) -> str:
    segment = path.strip("/").split("/")[-1] if path.strip("/") else ""
    return segment.split("~", 1)[0].strip()


def _jf_join(path: str, item: dict) -> str:
    seg = _jf_segment(item)
    return f"{path.strip('/')}/{seg}".strip("/")


def _jellyfin_rows(items: list[dict], path: str) -> list[dict]:
    rows: list[dict] = []
    for item in items:
        item_id = str(item.get("Id") or "")
        if not item_id:
            continue
        kind = str(item.get("Type") or "")
        is_dir = bool(item.get("IsFolder")) or kind not in {"Audio", "AudioBook"}
        artists = item.get("Artists") or []
        artist = ", ".join(str(x) for x in artists if x) or str(item.get("AlbumArtist") or "")
        ticks = item.get("RunTimeTicks")
        try:
            duration = float(ticks) / 10_000_000 if ticks is not None else None
        except (TypeError, ValueError):
            duration = None
        rows.append({
            "name": str(item.get("Name") or "Untitled"),
            "path": _jf_join(path, item),
            "is_dir": is_dir,
            "kind": kind,
            "artist": artist,
            "duration": duration,
        })
    return sorted(rows, key=lambda x: (not x["is_dir"], x["name"].lower()))


def browse_jellyfin(source: dict, path: str = "", query: str = "") -> list[dict]:
    token, user_id, _ = _jellyfin_login(source)
    parent_id = _jf_item_id(path)
    if query.strip():
        params = {
            "Recursive": "true",
            "SearchTerm": query.strip(),
            "IncludeItemTypes": "Audio",
            "Fields": "Path,MediaSources,Genres,Artists,AlbumArtist",
            "SortBy": "SortName",
            "SortOrder": "Ascending",
            "Limit": "250",
        }
        if parent_id:
            params["ParentId"] = parent_id
        r, _, _ = _jellyfin_request(source, "GET", f"/Users/{user_id}/Items", params=params)
        try:
            if r.status_code >= 400:
                raise RemoteSourceError(f"Jellyfin search returned HTTP {r.status_code}: {r.reason_phrase}")
            payload = r.json()
        finally:
            _close_jellyfin_response(r)
        return _jellyfin_rows(list(payload.get("Items") or []), path)

    if not parent_id:
        r, _, _ = _jellyfin_request(source, "GET", f"/Users/{user_id}/Views")
        try:
            if r.status_code >= 400:
                raise RemoteSourceError(f"Jellyfin library browse returned HTTP {r.status_code}: {r.reason_phrase}")
            payload = r.json()
        finally:
            _close_jellyfin_response(r)
        items = list(payload.get("Items") or [])
        music = [x for x in items if str(x.get("CollectionType") or "").lower() in {"music", "mixed"}]
        # Some older/custom servers omit CollectionType. In that case let the
        # user browse the visible views rather than falsely showing an empty root.
        if music:
            items = music
        return _jellyfin_rows(items, path)

    params = {
        "ParentId": parent_id,
        "Recursive": "false",
        "IncludeItemTypes": "Audio,Folder,MusicAlbum,MusicArtist,Playlist,CollectionFolder,UserView",
        "Fields": "Path,MediaSources,Genres,Artists,AlbumArtist",
        "SortBy": "SortName",
        "SortOrder": "Ascending",
        "Limit": "1000",
    }
    r, _, _ = _jellyfin_request(source, "GET", f"/Users/{user_id}/Items", params=params)
    try:
        if r.status_code >= 400:
            raise RemoteSourceError(f"Jellyfin browse returned HTTP {r.status_code}: {r.reason_phrase}")
        payload = r.json()
    finally:
        _close_jellyfin_response(r)
    return _jellyfin_rows(list(payload.get("Items") or []), path)


def _jellyfin_item(source: dict, remote_path: str) -> tuple[dict, str]:
    _, user_id, _ = _jellyfin_login(source)
    item_id = _jf_item_id(remote_path)
    if not item_id:
        raise RemoteSourceError("Invalid Jellyfin item")
    r, _, _ = _jellyfin_request(source, "GET", f"/Users/{user_id}/Items/{item_id}")
    try:
        if r.status_code == 404:
            raise RemoteSourceError("Jellyfin track no longer exists")
        if r.status_code >= 400:
            raise RemoteSourceError(f"Jellyfin item lookup returned HTTP {r.status_code}: {r.reason_phrase}")
        return r.json(), user_id
    finally:
        _close_jellyfin_response(r)


def _jellyfin_extension(item: dict) -> str:
    path = str(item.get("Path") or "")
    suffix = Path(path).suffix.lower()
    if suffix in AUDIO_EXTENSIONS:
        return suffix
    containers = [x.strip().lower() for x in str(item.get("Container") or "").split(",") if x.strip()]
    mapping = {"mp3": ".mp3", "flac": ".flac", "wav": ".wav", "ogg": ".ogg", "oga": ".oga", "opus": ".opus", "m4a": ".m4a", "mp4": ".m4a", "aac": ".aac", "wma": ".wma"}
    for c in containers:
        if c in mapping:
            return mapping[c]
    return ".mp3"


def download_jellyfin_audio(source: dict, remote_path: str, user_id: int) -> tuple[Path, float | None, str, str]:
    item, jellyfin_user_id = _jellyfin_item(source, remote_path)
    if str(item.get("Type") or "") not in {"Audio", "AudioBook"}:
        raise RemoteSourceError("Selected Jellyfin item is not an audio track")
    item_id = str(item.get("Id") or _jf_item_id(remote_path))
    suffix = _jellyfin_extension(item)
    target_dir = USER_DATA_DIR / str(user_id) / "remote" / "jellyfin"
    target_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{source['id']}:{item_id}".encode()).hexdigest()[:24]
    target = target_dir / f"{digest}{suffix}"
    if target.exists():
        d, ok = ffprobe(target)
        if ok:
            t, a = read_tags(target)
            title = str(item.get("Name") or t or target.stem)
            artists = item.get("Artists") or []
            artist = ", ".join(str(x) for x in artists if x) or str(item.get("AlbumArtist") or a or "")
            return target, d, title, artist

    tmp = target.with_suffix(target.suffix + ".part")
    tmp.unlink(missing_ok=True)
    r, _, _ = _jellyfin_request(source, "GET", f"/Audio/{item_id}/stream", stream=True, params={"Static": "true", "UserId": jellyfin_user_id})
    total = 0
    try:
        if r.status_code in {401, 403}:
            raise RemoteAuthError("Jellyfin allowed login but refused this audio stream for the selected user")
        if r.status_code >= 400:
            raise RemoteSourceError(f"Jellyfin audio stream returned HTTP {r.status_code}: {r.reason_phrase}")
        with tmp.open("wb") as h:
            for chunk in r.iter_bytes(262144):
                total += len(chunk)
                if total > MAX_REMOTE_BYTES:
                    raise RemoteSourceError("Jellyfin track exceeded MAX_REMOTE_MB")
                h.write(chunk)
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        _close_jellyfin_response(r)

    d, ok = ffprobe(target)
    if not ok:
        target.unlink(missing_ok=True)
        raise RemoteSourceError("Jellyfin stream did not produce playable audio")
    tag_title, tag_artist = read_tags(target)
    title = str(item.get("Name") or tag_title or target.stem)
    artists = item.get("Artists") or []
    artist = ", ".join(str(x) for x in artists if x) or str(item.get("AlbumArtist") or tag_artist or "")
    return target, d, title, artist


def browse_source(source: dict, path: str = "", query: str = "") -> list[dict]:
    if source["kind"] == "webdav":
        return browse_webdav(source, path, query)
    if source["kind"] == "ftp":
        return browse_ftp(source, path, query)
    if source["kind"] == "jellyfin":
        return browse_jellyfin(source, path, query)
    raise RemoteSourceError("Unsupported source type")


def source_folder_label(source: dict, remote_path: str) -> str:
    """Human-friendly folder name for a cached remote track."""
    if source.get("kind") == "jellyfin":
        parts = remote_path.strip("/").split("/")[:-1]
        labels = []
        for part in parts:
            encoded = part.split("~", 1)[1] if "~" in part else part
            labels.append(unquote(encoded))
        suffix = "/".join(x for x in labels if x)
    else:
        parent = posixpath.dirname(remote_path.strip("/"))
        suffix = parent.strip("/")
    return f"{source.get('name') or 'Remote'}/{suffix}".rstrip("/")


def test_source(source: dict) -> dict:
    rows = browse_source(source, "")
    if source.get("kind") == "jellyfin":
        _, _, user_name = _jellyfin_login(source)
        return {"ok": True, "entries": len(rows), "message": f"Connected to Jellyfin as {user_name}. {len(rows)} visible music libraries."}
    return {"ok": True, "entries": len(rows), "message": f"Connected. {len(rows)} visible entries in the root folder."}


def download_source_audio(source: dict, remote_path: str, user_id: int) -> tuple[Path, float | None, str, str]:
    if source.get("kind") == "jellyfin":
        return download_jellyfin_audio(source, remote_path, user_id)
    if Path(remote_path).suffix.lower() not in AUDIO_EXTENSIONS:
        raise RemoteSourceError("Not a supported audio file")
    target_dir = USER_DATA_DIR / str(user_id) / "remote"
    target_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{source['id']}:{remote_path}".encode()).hexdigest()[:24]
    target = target_dir / f"{digest}{Path(remote_path).suffix.lower()}"
    if target.exists():
        d, ok = ffprobe(target)
        if ok:
            t, a = read_tags(target)
            return target, d, t, a

    tmp = target.with_suffix(target.suffix + ".part")
    tmp.unlink(missing_ok=True)
    total = 0
    try:
        if source["kind"] == "webdav":
            joined = posixpath.join(source.get("root_path", "").strip("/"), remote_path.strip("/")).strip("/")
            url = _webdav_url(source["url"], joined)
            username, password = _source_credentials(source)
            auths = [httpx.BasicAuth(username, password)] if username else [None]
            completed = False
            for attempt, auth in enumerate(auths + ([httpx.DigestAuth(username, password)] if username else [])):
                with httpx.Client(auth=auth, follow_redirects=True, timeout=httpx.Timeout(90.0, connect=15.0)) as client:
                    with client.stream("GET", url) as r:
                        if r.status_code == 401:
                            challenge = r.headers.get("www-authenticate", "")
                            if attempt == 0 and username and "digest" in challenge.lower():
                                continue
                            offered = challenge.split(" ", 1)[0] if challenge else "unknown"
                            raise RemoteAuthError(f"WebDAV authentication failed (HTTP 401, server auth: {offered}). Check the WebDAV username/password or app password.")
                        if r.status_code >= 400:
                            raise RemoteSourceError(f"WebDAV download returned HTTP {r.status_code}: {r.reason_phrase}")
                        with tmp.open("wb") as h:
                            for chunk in r.iter_bytes(262144):
                                total += len(chunk)
                                if total > MAX_REMOTE_BYTES:
                                    raise RemoteSourceError("Remote file exceeded MAX_REMOTE_MB")
                                h.write(chunk)
                        completed = True
                        break
                if completed:
                    break
            if not completed:
                raise RemoteAuthError("WebDAV authentication failed")
        elif source["kind"] == "ftp":
            ftp, base = _ftp_connect(source)
            full = "/".join(x for x in [base, source.get("root_path", "").strip("/"), remote_path.strip("/")] if x)
            try:
                with tmp.open("wb") as h:
                    def writer(data: bytes):
                        nonlocal total
                        total += len(data)
                        if total > MAX_REMOTE_BYTES:
                            raise RemoteSourceError("Remote file exceeded MAX_REMOTE_MB")
                        h.write(data)
                    ftp.retrbinary(f"RETR {full}", writer)
            except ftplib.error_perm as exc:
                if str(exc).startswith("530"):
                    raise RemoteAuthError(f"FTP authentication failed: {exc}") from exc
                raise RemoteSourceError(f"FTP download failed: {exc}") from exc
            finally:
                try:
                    ftp.quit()
                except Exception:
                    ftp.close()
        else:
            raise RemoteSourceError("Unsupported source type")
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    d, ok = ffprobe(target)
    if not ok:
        target.unlink(missing_ok=True)
        raise RemoteSourceError("Remote file is not playable audio")
    t, a = read_tags(target)
    return target, d, t, a
