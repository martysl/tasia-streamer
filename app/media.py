from __future__ import annotations

import base64
import hashlib
import html
import json
import secrets
import time
import ipaddress
import os
import re
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx
from mutagen import File as MutagenFile

from .config import ALLOW_PRIVATE_URLS, AUDIO_EXTENSIONS, MAX_REMOTE_BYTES, USER_DATA_DIR
from . import db

UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
SUNO_CDN_RE = re.compile(r"https?://cdn1\.suno\.ai/([0-9a-fA-F-]{36})\.mp3", re.I)
SUNO_SONG_RE = re.compile(r"https?://(?:www\.)?suno\.com/song/([0-9a-fA-F-]{36})", re.I)
SUNO_AUDIO_RE = re.compile(
    r"https?://cdn1\.suno\.ai/[0-9a-fA-F-]{36}\.mp3(?:\?[^\"'<>\\\s]+)?",
    re.I,
)
SUNO_HOSTS = {
    "suno.com", "www.suno.com", "cdn1.suno.ai", "cdn2.suno.ai",
    "studio-api-prod.suno.com", "studio-api.prod.suno.com",
}
# Suno currently uses both spellings in the wild. Their web app / wrappers have
# migrated between them, so try both instead of baking in one fragile hostname.
SUNO_API_BASES = ("https://studio-api-prod.suno.com", "https://studio-api.prod.suno.com")
SUNO_API_BASE = SUNO_API_BASES[0]
CLERK_BASE = "https://auth.suno.com"
CLERK_JS_VERSION = "5.117.0"
CLERK_API_VERSION = "2025-11-10"


def ffprobe(path: Path) -> tuple[float | None, bool]:
    cmd = ["ffprobe","-v","error","-select_streams","a:0","-show_entries","stream=codec_type","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None, False
    if result.returncode != 0: return None, False
    lines=[x.strip() for x in result.stdout.splitlines() if x.strip()]
    has_audio=any(x=="audio" for x in lines); duration=None
    for x in lines:
        try: duration=float(x); break
        except ValueError: pass
    return duration, has_audio


def read_tags(path: Path) -> tuple[str,str]:
    title=path.stem; artist=""
    try:
        audio=MutagenFile(path,easy=True)
        if audio and getattr(audio,"tags",None):
            tv=audio.tags.get("title") or []; av=audio.tags.get("artist") or []
            if tv: title=str(tv[0]).strip() or title
            if av: artist=str(av[0]).strip()
    except Exception: pass
    return title,artist


def _assert_public_http_url(url: str) -> None:
    parsed=urlparse(url)
    if parsed.scheme not in {"http","https"} or not parsed.hostname: raise ValueError("Only http:// or https:// URLs are accepted")
    if ALLOW_PRIVATE_URLS: return
    try: addresses=socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme=="https" else 80))
    except socket.gaierror as exc: raise ValueError(f"Cannot resolve URL hostname: {exc}") from exc
    for addr in addresses:
        ip=ipaddress.ip_address(addr[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("Private/local network URLs are blocked; set ALLOW_PRIVATE_URLS=true if required")


def _suno_secret_dir(user_id: int) -> Path:
    p=USER_DATA_DIR/str(int(user_id))/"secrets"
    p.mkdir(parents=True,exist_ok=True)
    return p


def _suno_cookie_path(user_id: int) -> Path:
    return _suno_secret_dir(user_id)/"suno-cookies.txt"


def _suno_session_path(user_id: int) -> Path:
    return _suno_secret_dir(user_id)/"suno-session.json"


def _load_suno_session(user_id: int) -> dict:
    p=_suno_session_path(user_id)
    if not p.exists(): return {}
    try:
        data=json.loads(p.read_text("utf-8"))
        return data if isinstance(data,dict) else {}
    except Exception:
        return {}


def _write_suno_session(user_id: int, data: dict) -> None:
    p=_suno_session_path(user_id)
    tmp=p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    os.chmod(tmp,0o600)
    os.replace(tmp,p)
    os.chmod(p,0o600)


SUNO_CONNECTOR_STATE_KEY = "suno_connector_key"


def get_suno_connector_key(user_id: int) -> str:
    """Return a stable per-user connector key stored in SQLite.

    beta23 stored this key inside suno-session.json. That made key creation
    depend on the secrets directory being writable and caused a blank key in
    Settings on some bind-mounted installs. beta24 stores the connector key in
    user_state instead. If an old beta23 key exists, migrate it once so an
    already-configured browser connector keeps working.
    """
    key=str(db.get_state(user_id,SUNO_CONNECTOR_STATE_KEY,"") or "").strip()
    if len(key)>=20:
        return key

    # Preserve an existing beta23 key when possible. Reading the legacy session
    # may fail on a damaged/readonly secrets directory; key generation itself
    # must still succeed because SQLite is already required for the app to run.
    try:
        legacy=_load_suno_session(user_id)
        legacy_key=str(legacy.get("connector_key") or "").strip()
    except OSError:
        legacy_key=""
    key=legacy_key if len(legacy_key)>=20 else secrets.token_urlsafe(32)
    db.set_state(user_id,SUNO_CONNECTOR_STATE_KEY,key)
    return key


def rotate_suno_connector_key(user_id: int) -> str:
    key=secrets.token_urlsafe(32)
    db.set_state(user_id,SUNO_CONNECTOR_STATE_KEY,key)
    # Best-effort mirror for beta23 compatibility/debugging; never make key
    # generation fail just because the secrets directory cannot be written.
    try:
        data=_load_suno_session(user_id)
        data["connector_key"]=key
        _write_suno_session(user_id,data)
    except OSError:
        pass
    return key


def _normalize_suno_client_cookie(value: str) -> str:
    raw=str(value or "").strip()
    if raw.lower().startswith("cookie:"):
        raw=raw.split(":",1)[1].strip()
    # Accept either the raw Clerk value or a complete Cookie header pasted by
    # the user, but retain only __client. The connector sends only this value.
    if ";" in raw or "=" in raw:
        for part in raw.split(";"):
            name,sep,val=part.strip().partition("=")
            if sep and name.strip()=="__client":
                raw=val.strip(); break
        else:
            if raw.startswith("__client="):
                raw=raw.split("=",1)[1].strip()
    if len(raw)<20 or len(raw)>20000:
        raise ValueError("Suno __client session cookie does not look valid")
    return raw


def _sanitize_suno_device_id(value: str|None) -> str:
    did=str(value or "").strip().replace("%22",'"').strip('"\' ').strip()
    if UUID_RE.fullmatch(did):
        return did.lower()
    import uuid as _uuid
    return str(_uuid.uuid4())


def _jwt_expiry_epoch(token: str) -> int|None:
    try:
        parts=str(token or "").split(".")
        if len(parts)!=3: return None
        payload=parts[1] + "="*((4-len(parts[1])%4)%4)
        data=json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        exp=data.get("exp")
        return int(exp) if isinstance(exp,(int,float)) else None
    except Exception:
        return None


def _clerk_headers(client_cookie: str) -> dict[str,str]:
    return {
        "authorization":client_cookie,
        "cookie":f"__client={client_cookie}",
        "origin":"https://suno.com",
        "referer":"https://suno.com/",
        "user-agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
    }


def _clerk_client_url() -> str:
    return f"{CLERK_BASE}/v1/client?__clerk_api_version={CLERK_API_VERSION}&_clerk_js_version={CLERK_JS_VERSION}"


def _clerk_token_url(session_id: str) -> str:
    return f"{CLERK_BASE}/v1/client/sessions/{session_id}/tokens?__clerk_api_version={CLERK_API_VERSION}&_clerk_js_version={CLERK_JS_VERSION}"


def _clerk_session_id(client: httpx.Client, client_cookie: str) -> str:
    r=client.get(_clerk_client_url(),headers=_clerk_headers(client_cookie))
    if not r.is_success:
        raise ValueError(f"Suno Clerk session lookup failed with HTTP {r.status_code}")
    try: body=r.json()
    except ValueError as exc: raise ValueError("Suno Clerk returned invalid session JSON") from exc
    response=body.get("response") if isinstance(body,dict) else None
    sid=response.get("last_active_session_id") if isinstance(response,dict) else None
    if not sid and isinstance(response,dict):
        sessions=response.get("sessions")
        if isinstance(sessions,list) and sessions and isinstance(sessions[0],dict): sid=sessions[0].get("id")
    if not sid:
        raise ValueError("Suno Clerk found no active session. Log into suno.com in the browser and reconnect.")
    return str(sid)


def _clerk_refresh_jwt(client: httpx.Client, client_cookie: str, session_id: str) -> str:
    headers=_clerk_headers(client_cookie); headers["content-type"]="application/x-www-form-urlencoded"
    r=client.post(_clerk_token_url(session_id),headers=headers,content=b"")
    if not r.is_success:
        raise ValueError(f"Suno Clerk JWT refresh failed with HTTP {r.status_code}")
    try: body=r.json()
    except ValueError as exc: raise ValueError("Suno Clerk returned invalid JWT JSON") from exc
    jwt=body.get("jwt") if isinstance(body,dict) else None
    if not jwt:
        raise ValueError("Suno Clerk returned no JWT. Reconnect the browser session.")
    return str(jwt)


def _refresh_suno_jwt(user_id: int, *, recover_session: bool=True) -> str:
    data=_load_suno_session(user_id)
    client_cookie=str(data.get("clerk_client_cookie") or "").strip()
    if not client_cookie:
        # beta23-26 legacy sessions can still work until their Bearer expires.
        token=str(data.get("bearer_token") or data.get("jwt") or "").strip()
        if token: return token
        raise ValueError("Suno is not connected with a refreshable browser session")
    session_id=str(data.get("session_id") or "").strip()
    with httpx.Client(follow_redirects=True,timeout=httpx.Timeout(25.0,connect=10.0)) as client:
        if not session_id:
            session_id=_clerk_session_id(client,client_cookie)
        try:
            jwt=_clerk_refresh_jwt(client,client_cookie,session_id)
        except ValueError:
            if not recover_session: raise
            session_id=_clerk_session_id(client,client_cookie)
            jwt=_clerk_refresh_jwt(client,client_cookie,session_id)
    data["session_id"]=session_id
    data["jwt"]=jwt
    data.pop("bearer_token",None)
    data["jwt_refreshed_at_epoch"]=time.time()
    data["updated_at_epoch"]=time.time()
    _write_suno_session(user_id,data)
    return jwt


def _ensure_suno_jwt(user_id: int, *, force: bool=False) -> str:
    data=_load_suno_session(user_id)
    token=str(data.get("jwt") or data.get("bearer_token") or "").strip()
    refreshable=bool(data.get("clerk_client_cookie"))
    exp=_jwt_expiry_epoch(token) if token else None
    # suno-cli observed server-side staleness before JWT exp; refresh when less
    # than 30 minutes remain, or whenever the API asks us to retry.
    stale=not token or exp is None or (time.time()+1800>=exp)
    if refreshable and (force or stale):
        return _refresh_suno_jwt(user_id)
    if token: return token
    raise ValueError("Suno is not connected. Use the Tasia Suno Connector while logged into suno.com.")


def save_suno_browser_session(user_id: int, client_cookie: str, device_id: str|None=None) -> dict:
    cookie=_normalize_suno_client_cookie(client_cookie)
    data=_load_suno_session(user_id)
    data["clerk_client_cookie"]=cookie
    data["device_id"]=_sanitize_suno_device_id(device_id or data.get("device_id"))
    data.pop("bearer_token",None)
    data.pop("jwt",None)
    data.pop("session_id",None)
    data["updated_at_epoch"]=time.time()
    get_suno_connector_key(user_id)
    _write_suno_session(user_id,data)
    # Validate immediately so the connector can say Connected only when the
    # cookie can actually mint a JWT from Clerk.
    _refresh_suno_jwt(user_id)
    return suno_auth_status(user_id)


def refresh_suno_session(user_id: int) -> dict:
    _refresh_suno_jwt(user_id)
    return suno_auth_status(user_id)


def save_suno_session(user_id: int, bearer_token: str, device_id: str|None=None) -> dict:
    """Legacy manual Bearer fallback for beta23-26 users.

    New installations should use save_suno_browser_session(), because a raw
    Bearer cannot refresh itself.
    """
    token=str(bearer_token or "").strip()
    if token.lower().startswith("bearer "): token=token[7:].strip()
    if len(token)<20 or len(token)>20000:
        raise ValueError("Suno Bearer token does not look valid")
    data=_load_suno_session(user_id)
    data["bearer_token"]=token
    data["device_id"]=_sanitize_suno_device_id(device_id or data.get("device_id"))
    get_suno_connector_key(user_id)
    data["updated_at_epoch"]=time.time()
    _write_suno_session(user_id,data)
    return suno_auth_status(user_id)

def clear_suno_session(user_id: int, *, keep_connector_key: bool=True) -> None:
    # Suno auth state and connector pairing are intentionally separate.
    # Disconnecting Suno should not break the browser connector pairing.
    if keep_connector_key:
        get_suno_connector_key(user_id)
    else:
        db.set_state(user_id,SUNO_CONNECTOR_STATE_KEY,"")
    try:
        _suno_session_path(user_id).unlink(missing_ok=True)
    except OSError:
        pass


def suno_cookie_status(user_id: int) -> dict:
    p=_suno_cookie_path(user_id)
    if not p.exists() or p.stat().st_size == 0:
        return {"cookies_set":False,"mode":None}
    text=p.read_text("utf-8",errors="replace").lstrip()
    mode="netscape" if (text.startswith("# Netscape HTTP Cookie File") or "\t" in text) else "header"
    return {"cookies_set":True,"mode":mode}


def suno_auth_status(user_id: int) -> dict:
    data=_load_suno_session(user_id)
    cookie=suno_cookie_status(user_id)
    updated=data.get("updated_at_epoch")
    jwt=str(data.get("jwt") or data.get("bearer_token") or "")
    exp=_jwt_expiry_epoch(jwt)
    refreshable=bool(data.get("clerk_client_cookie"))
    return {
        "connected":bool(jwt or refreshable),
        "refreshable":refreshable,
        "auth_mode":"clerk" if refreshable else ("bearer" if jwt else None),
        "device_id":str(data.get("device_id") or ""),
        "connector_key":get_suno_connector_key(user_id),
        "updated_at_epoch":float(updated) if isinstance(updated,(int,float)) else None,
        "jwt_expires_at_epoch":exp,
        "cookies_set":cookie["cookies_set"],
        "cookie_mode":cookie["mode"],
    }

def save_suno_cookies(user_id: int, data: bytes) -> dict:
    if not data: raise ValueError("Suno cookie data is empty")
    if len(data)>512*1024: raise ValueError("Suno cookie data is larger than 512 KB")
    text=data.decode("utf-8",errors="replace").strip()
    if text.lower().startswith("cookie:"):
        text=text.split(":",1)[1].strip()
    is_netscape=text.startswith("# Netscape HTTP Cookie File") or "\t" in text
    if not text or (not is_netscape and "=" not in text):
        raise ValueError("Paste a Cookie header or upload a Netscape cookies.txt export")
    p=_suno_cookie_path(user_id)
    p.write_text(text+"\n",encoding="utf-8")
    os.chmod(p,0o600)
    return suno_cookie_status(user_id)


def clear_suno_cookies(user_id: int) -> None:
    _suno_cookie_path(user_id).unlink(missing_ok=True)


def _suno_cookie_auth(user_id: int) -> tuple[dict[str,str], httpx.Cookies | None]:
    p=_suno_cookie_path(user_id)
    if not p.exists() or p.stat().st_size == 0:
        return {},None
    text=p.read_text("utf-8",errors="replace").strip()
    if text.startswith("# Netscape HTTP Cookie File") or "\t" in text:
        jar=httpx.Cookies(); loaded=0
        for line in text.splitlines():
            line=line.strip("\r\n")
            if not line: continue
            if line.startswith("#HttpOnly_"): line=line[len("#HttpOnly_"):]
            elif line.startswith("#"): continue
            parts=line.split("\t")
            if len(parts)<7: continue
            domain,_,path,_,_,name,value=parts[:7]
            if not name: continue
            try:
                jar.set(name,value,domain=domain or None,path=path or "/"); loaded+=1
            except Exception: pass
        if loaded: return {},jar
    raw=text.split(":",1)[1].strip() if text.lower().startswith("cookie:") else text
    return {"Cookie":raw},None


def _suno_page_client(user_id: int, *, timeout: httpx.Timeout | None=None) -> httpx.Client:
    extra_headers,cookies=_suno_cookie_auth(user_id)
    headers={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36","Accept":"*/*",**extra_headers}
    return httpx.Client(follow_redirects=True,timeout=timeout or httpx.Timeout(20.0,connect=10.0),headers=headers,cookies=cookies)


def _suno_browser_token() -> str:
    raw=json.dumps({"timestamp":int(time.time()*1000)},separators=(",",":")).encode("utf-8")
    token=base64.b64encode(raw).decode("ascii")
    return json.dumps({"token":token},separators=(",",":"))


def _suno_api_headers(user_id: int, *, force_refresh: bool=False) -> dict[str,str]:
    session=_load_suno_session(user_id)
    token=_ensure_suno_jwt(user_id,force=force_refresh)
    session=_load_suno_session(user_id)
    device_id=_sanitize_suno_device_id(session.get("device_id"))
    if device_id!=session.get("device_id"):
        session["device_id"]=device_id; _write_suno_session(user_id,session)
    return {
        "accept":"*/*",
        "accept-language":"en-US,en;q=0.8",
        "authorization":f"Bearer {token}",
        "browser-token":_suno_browser_token(),
        "cache-control":"no-cache",
        "device-id":device_id,
        "origin":"https://suno.com",
        "pragma":"no-cache",
        "referer":"https://suno.com/",
        "user-agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
    }

def _suno_api_client(user_id: int, *, timeout: httpx.Timeout|None=None) -> httpx.Client:
    return httpx.Client(follow_redirects=True,timeout=timeout or httpx.Timeout(25.0,connect=10.0),headers=_suno_api_headers(user_id))


def _pick_suno_track(data) -> dict|None:
    if isinstance(data,list):
        return data[0] if data and isinstance(data[0],dict) else None
    if isinstance(data,dict):
        clips=data.get("clips")
        if isinstance(clips,list) and clips and isinstance(clips[0],dict): return clips[0]
        if isinstance(data.get("clip"),dict): return data["clip"]
        if data.get("id") or data.get("audio_url"): return data
        for key in ("data","result"):
            nested=data.get(key)
            found=_pick_suno_track(nested)
            if found: return found
    return None


def _suno_forbidden_url(url: str) -> bool:
    try:
        parsed=urlparse(str(url or ""))
    except Exception:
        return True
    host=(parsed.hostname or "").lower()
    path=(parsed.path or "").lower().rstrip("/")
    return host in {"studio-api.prod.suno.com","studio-api-prod.suno.com"} and path.endswith("/api/forbidden")


def _suno_track_audio_candidates(track: dict) -> list[str]:
    """Return only API-provided audio URLs; never synthesize a CDN path.

    Current suno-cli downloads clip.audio_url directly. Keep audio_url first,
    then audio_url_2/explicit alternates, and only use media_urls as a final
    compatibility fallback. Explicit /api/forbidden placeholders are dropped.
    """
    out=[]; seen=set()
    def add(value):
        u=str(value or "").strip()
        if not u.startswith(("http://","https://")) or u in seen or _suno_forbidden_url(u): return
        seen.add(u); out.append(u)
    add(track.get("audio_url"))
    add(track.get("audio_url_2"))
    for key in ("stream_audio_url","download_url","wav_file_url"):
        add(track.get(key))
    for holder in (track, track.get("metadata") if isinstance(track.get("metadata"),dict) else None):
        if not isinstance(holder,dict): continue
        media=holder.get("media_urls")
        if isinstance(media,list):
            for item in media:
                add(item.get("url") if isinstance(item,dict) else item)
    return out

def _suno_track_audio_url(track: dict) -> str|None:
    urls=_suno_track_audio_candidates(track)
    return urls[0] if urls else None


def _suno_response_is_auth_stale(response: httpx.Response) -> bool:
    if response.status_code==401: return True
    if response.status_code!=403: return False
    try: body=response.text.lower()
    except Exception: body=""
    return any(x in body for x in ("token validation failed","jwt expired","invalid jwt","invalid token","not authenticated","unauthenticated"))


def _suno_api_request(user_id: int, method: str, url: str, **kwargs) -> httpx.Response:
    last=None
    for attempt in range(2):
        headers=_suno_api_headers(user_id,force_refresh=(attempt==1))
        try:
            with httpx.Client(follow_redirects=True,timeout=httpx.Timeout(25.0,connect=10.0),headers=headers) as client:
                r=client.request(method,url,**kwargs)
        except httpx.RequestError as exc:
            raise ValueError(f"Could not reach Suno API: {exc}") from exc
        last=r
        if attempt==0 and _suno_response_is_auth_stale(r) and _load_suno_session(user_id).get("clerk_client_cookie"):
            continue
        return r
    return last


def suno_track(user_id: int, clip_id: str) -> dict:
    uid=str(clip_id or "").strip().lower()
    if not UUID_RE.fullmatch(uid): raise ValueError("Invalid Suno clip UUID")
    last_error=""
    # This is the same feed-by-id shape used by current suno-cli polling/info.
    # Prefer the canonical hyphenated host and retain the dotted spelling only
    # as compatibility fallback.
    for base in SUNO_API_BASES:
        r=_suno_api_request(user_id,"GET",f"{base}/api/feed/",params={"ids":uid})
        if not r.is_success:
            last_error=f"HTTP {r.status_code} from Suno feed"
            continue
        try: data=r.json()
        except ValueError:
            last_error="Suno feed returned invalid JSON"; continue
        track=_pick_suno_track(data)
        if not track:
            last_error="Suno feed returned no matching clip"; continue
        candidates=_suno_track_audio_candidates(track)
        if not candidates:
            last_error="Suno clip has no usable audio_url"; continue
        result=dict(track)
        result["audio_url"]=candidates[0]
        result["tasia_audio_candidates"]=candidates
        result["tasia_api_url"]=str(r.url)
        return result
    raise ValueError("Suno returned no usable audio URL. " + (last_error or "Reconnect Suno and try again."))

def _suno_billing_touch(user_id: int, clip_id: str) -> None:
    # Non-critical bookkeeping request observed in Suno's web exporter.
    for base in SUNO_API_BASES:
        try:
            r=_suno_api_request(user_id,"POST",f"{base}/api/billing/clips/{clip_id}/download/")
            if r.is_success: return
        except Exception:
            pass

def _clean_suno_embedded_text(text: str) -> str:
    return html.unescape(text).replace("\\u0026","&").replace("\\u003d","=").replace("\\/","/")


def _extract_suno_audio_url(text: str) -> str | None:
    clean=_clean_suno_embedded_text(text)
    m=SUNO_AUDIO_RE.search(clean)
    if not m: return None
    return m.group(0).rstrip("\\,})]")


def _resolve_suno_page(page_url: str, user_id: int) -> tuple[str | None, str | None]:
    with _suno_page_client(user_id) as client:
        r=client.get(page_url); r.raise_for_status()
        final=str(r.url); audio=_extract_suno_audio_url(final) or _extract_suno_audio_url(r.text); uid=None
        for candidate in (audio,final,r.text):
            if not candidate: continue
            m=SUNO_CDN_RE.search(candidate) or SUNO_SONG_RE.search(candidate) or UUID_RE.search(candidate)
            if m:
                uid=m.group(1).lower() if m.lastindex else m.group(0).lower(); break
        return audio,uid


def _suno_uuid_from_value(value: str) -> str|None:
    value=str(value or "").strip()
    if UUID_RE.fullmatch(value): return value.lower()
    m=SUNO_CDN_RE.search(value) or SUNO_SONG_RE.search(value)
    return m.group(1).lower() if m else None


def resolve_suno_candidates(value: str, user_id: int | None=None) -> tuple[list[str],str|None]:
    """Return ordered playable candidates plus stable Suno UUID."""
    value=value.strip(); parsed=urlparse(value)
    m=SUNO_CDN_RE.search(value)
    if m and parsed.query:
        return [value],m.group(1).lower()

    uid=_suno_uuid_from_value(value)
    is_suno_page=bool(parsed.hostname and parsed.hostname.lower() in {"suno.com","www.suno.com"})
    if not uid and is_suno_page:
        if user_id is None: raise ValueError("Suno share-link resolution needs the current Tasia user")
        try:
            _,uid=_resolve_suno_page(value,user_id)
        except httpx.HTTPStatusError as exc:
            raise ValueError(f"Suno share page returned HTTP {exc.response.status_code}") from exc
        if not uid: raise ValueError("Could not find a Suno clip UUID in this share link")

    if uid:
        if user_id is not None:
            try:
                track=suno_track(user_id,uid)
                urls=list(track.get("tasia_audio_candidates") or [])
                if urls: return urls,uid
            except ValueError as api_error:
                if m and parsed.query: return [value],uid
                try:
                    audio,page_uid=_resolve_suno_page(f"https://suno.com/song/{uid}",user_id)
                    if audio and not _suno_forbidden_url(audio): return [audio],page_uid or uid
                except Exception: pass
                raise api_error
        raise ValueError("Suno UUID playback requires a connected Suno session")

    return [value],None


def resolve_suno_url(value: str, user_id: int | None=None) -> tuple[str,str|None]:
    urls,uid=resolve_suno_candidates(value,user_id)
    if not urls: raise ValueError("No playable URL resolved")
    return urls[0],uid

def user_cache_dir(user_id: int) -> Path:
    p=USER_DATA_DIR/str(user_id)/"cache"; p.mkdir(parents=True,exist_ok=True); return p


def cache_remote_audio(url: str, user_id: int, filename_hint: str|None=None) -> tuple[Path,float|None,str|None]:
    candidates,suno_uuid=resolve_suno_candidates(url,user_id)
    if not candidates: raise ValueError("No playable media URL resolved")

    cache=user_cache_dir(user_id)
    # Signed Suno URLs expire. Cache by UUID so refreshing/signature changes do
    # not create duplicate copies of the same track.
    cache_key=f"suno:{suno_uuid}" if suno_uuid else candidates[0]
    digest=hashlib.sha256(cache_key.encode()).hexdigest()[:24]
    final=cache/f"{digest}.mp3"
    if final.exists():
        duration,ok=ffprobe(final)
        if ok: return final,duration,suno_uuid

    raw=cache/f".{digest}.download"; tmp=cache/f".{digest}.tmp.mp3"
    raw.unlink(missing_ok=True); tmp.unlink(missing_ok=True)

    if suno_uuid:
        _suno_billing_touch(user_id,suno_uuid)

    errors=[]
    for idx,resolved in enumerate(candidates,1):
        raw.unlink(missing_ok=True); tmp.unlink(missing_ok=True)
        try:
            if _suno_forbidden_url(resolved):
                errors.append(f"candidate {idx}: Suno /api/forbidden placeholder")
                continue
            _assert_public_http_url(resolved)
            parsed=urlparse(resolved)
            is_suno=bool(suno_uuid) or (parsed.hostname or "").lower() in SUNO_HOSTS

            # Never send the Bearer JWT to media/CDN hosts. Legacy signed cookies
            # can still be supplied if the user configured them, but the normal
            # beta27 path relies on the fresh API-provided audio_url itself.
            if is_suno:
                extra_headers,cookies=_suno_cookie_auth(user_id)
                headers={
                    "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
                    "Accept":"audio/*,*/*;q=0.8",
                    "Referer":"https://suno.com/",
                    **extra_headers,
                }
                client=httpx.Client(follow_redirects=True,timeout=httpx.Timeout(90.0,connect=15.0),headers=headers,cookies=cookies)
            else:
                client=httpx.Client(follow_redirects=True,timeout=httpx.Timeout(90.0,connect=15.0),headers={"User-Agent":"Tasia-Streamer/2.0"})

            total=0
            with client:
                with client.stream("GET",resolved) as response:
                    if response.status_code in {401,403}:
                        body=response.read().decode("utf-8",errors="replace")[:4000]
                        final_url=str(response.url)
                        if is_suno:
                            if _suno_forbidden_url(final_url):
                                raise ValueError("Suno redirected this media candidate to /api/forbidden")
                            if "Missing Key-Pair-Id" in body or "MissingKey" in body or "CloudFront" in body:
                                raise ValueError("Suno/CloudFront rejected this media candidate (missing/expired signature)")
                        response.raise_for_status()
                    response.raise_for_status()
                    cl=response.headers.get("content-length")
                    if cl and int(cl)>MAX_REMOTE_BYTES: raise ValueError("Remote file is larger than MAX_REMOTE_MB")
                    with raw.open("wb") as h:
                        for chunk in response.iter_bytes(1024*256):
                            total+=len(chunk)
                            if total>MAX_REMOTE_BYTES: raise ValueError("Remote file exceeded MAX_REMOTE_MB")
                            h.write(chunk)

            cmd=["ffmpeg","-hide_banner","-loglevel","error","-y","-i",str(raw),"-vn","-ac","2","-ar","44100","-codec:a","libmp3lame","-b:a","192k",str(tmp)]
            result=subprocess.run(cmd,capture_output=True,text=True,timeout=300,check=False)
            if result.returncode!=0:
                raise ValueError(f"FFmpeg could not decode candidate: {result.stderr.strip()[-300:]}")
            duration,ok=ffprobe(tmp)
            if not ok: raise ValueError("Downloaded candidate did not contain playable audio")
            os.replace(tmp,final)
            return final,duration,suno_uuid
        except (ValueError,httpx.HTTPError,OSError) as exc:
            # Do not leak signed query strings/tokens into logs/UI. Only retain
            # the candidate number + short reason and move on to the next media.
            errors.append(f"candidate {idx}: {str(exc)[:180]}")
            continue
        finally:
            raw.unlink(missing_ok=True); tmp.unlink(missing_ok=True)

    detail="; ".join(errors[-4:])
    if suno_uuid:
        raise ValueError(
            "Suno returned track metadata, but every available audio media URL was rejected. "
            f"Tried {len(candidates)} candidate(s). {detail}"
        )
    raise ValueError(f"Could not cache remote audio. {detail}")

def valid_local_audio(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
