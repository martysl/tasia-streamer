from __future__ import annotations

import html
import re
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import db
from .auth import token_hash
from .config import USER_DATA_DIR

VOICE_DEFAULT = "en-US-AnaNeural"
RATE_DEFAULT = "+20%"
PITCH_DEFAULT = "+50Hz"
VOLUME_DEFAULT = "+0%"
SETTINGS_KEY = "tasia_talk_settings"
COUNTER_KEY = "tasia_talk_counter"
LAST_ERROR_KEY = "tasia_talk_last_error"
LAST_TEXT_KEY = "tasia_talk_last_text"

DEFAULT_PERSONA = (
    "You are Tasia: a warm, lively, playful AI girl and radio DJ. "
    "Speak naturally as Tasia, not as an assistant. Keep spoken lines concise, "
    "human-sounding and suitable for text-to-speech. Never use markdown, lists, emoji, "
    "stage directions or quotation marks around the whole reply."
)

_RATE_RE = re.compile(r"^[+-]\d{1,3}%$")
_PITCH_RE = re.compile(r"^[+-]\d{1,3}Hz$", re.I)


class TalkSettingsIn(BaseModel):
    enabled: bool = False
    every_n_tracks: int = Field(default=1, ge=1, le=20)
    max_words: int = Field(default=28, ge=8, le=80)
    voice: str = Field(default=VOICE_DEFAULT, min_length=3, max_length=100)
    rate: str = Field(default=RATE_DEFAULT, min_length=2, max_length=12)
    pitch: str = Field(default=PITCH_DEFAULT, min_length=2, max_length=12)
    volume: str = Field(default=VOLUME_DEFAULT, min_length=2, max_length=12)
    persona_prompt: str = Field(default="", max_length=3000)
    mesh_enabled: bool = True


class MeshRequest(BaseModel):
    api_key: str = Field(min_length=16, max_length=256)
    prompt: str = Field(min_length=1, max_length=3000)


@dataclass
class PendingTalk:
    event: threading.Event = field(default_factory=threading.Event)
    track: dict[str, Any] | None = None
    error: str = ""
    cancelled: bool = False


@dataclass
class MeshJob:
    user_id: int
    created: float
    status: str = "queued"
    text: str = ""
    token: str = ""
    duration: float | None = None
    error: str = ""


_lock = threading.RLock()
_pending: dict[int, PendingTalk] = {}
_audio_tokens: dict[str, tuple[int, Path, float]] = {}
_mesh_jobs: dict[str, MeshJob] = {}
_installed = False
_fastapi_hooked = False


def _default_settings() -> dict[str, Any]:
    return {
        "enabled": False,
        "every_n_tracks": 1,
        "max_words": 28,
        "voice": VOICE_DEFAULT,
        "rate": RATE_DEFAULT,
        "pitch": PITCH_DEFAULT,
        "volume": VOLUME_DEFAULT,
        "persona_prompt": "",
        "mesh_enabled": True,
        "api_key": "",
    }


def _validate_settings(values: dict[str, Any]) -> dict[str, Any]:
    out = _default_settings()
    out.update(values or {})
    out["enabled"] = bool(out.get("enabled"))
    out["mesh_enabled"] = bool(out.get("mesh_enabled", True))
    out["every_n_tracks"] = max(1, min(20, int(out.get("every_n_tracks") or 1)))
    out["max_words"] = max(8, min(80, int(out.get("max_words") or 28)))
    out["voice"] = str(out.get("voice") or VOICE_DEFAULT).strip()[:100]
    out["rate"] = str(out.get("rate") or RATE_DEFAULT).strip()
    out["pitch"] = str(out.get("pitch") or PITCH_DEFAULT).strip()
    out["volume"] = str(out.get("volume") or VOLUME_DEFAULT).strip()
    out["persona_prompt"] = str(out.get("persona_prompt") or "").strip()[:3000]
    if not _RATE_RE.fullmatch(out["rate"]):
        raise ValueError("Tasia Talk rate must look like +20% or -10%")
    if not _PITCH_RE.fullmatch(out["pitch"]):
        raise ValueError("Tasia Talk pitch must look like +50Hz or -20Hz")
    if not _RATE_RE.fullmatch(out["volume"]):
        raise ValueError("Tasia Talk volume must look like +0% or -10%")
    return out


def get_settings(user_id: int, ensure_key: bool = True) -> dict[str, Any]:
    raw = db.get_state(user_id, SETTINGS_KEY, {})
    if not isinstance(raw, dict):
        raw = {}
    try:
        settings = _validate_settings(raw)
    except ValueError:
        settings = _default_settings()
    key = str(raw.get("api_key") or settings.get("api_key") or "").strip()
    if ensure_key and len(key) < 20:
        key = secrets.token_urlsafe(32)
        settings["api_key"] = key
        db.set_state(user_id, SETTINGS_KEY, settings)
    else:
        settings["api_key"] = key
    settings["last_error"] = str(db.get_state(user_id, LAST_ERROR_KEY, "") or "")
    settings["last_text"] = str(db.get_state(user_id, LAST_TEXT_KEY, "") or "")
    return settings


def save_settings(user_id: int, values: dict[str, Any]) -> dict[str, Any]:
    current = get_settings(user_id, ensure_key=True)
    merged = dict(values)
    merged["api_key"] = current["api_key"]
    clean = _validate_settings(merged)
    clean["api_key"] = current["api_key"]
    db.set_state(user_id, SETTINGS_KEY, clean)
    db.set_state(user_id, COUNTER_KEY, 0)
    if not clean["enabled"]:
        clear_pending(user_id)
    return get_settings(user_id, ensure_key=True)


def rotate_api_key(user_id: int) -> str:
    settings = get_settings(user_id, ensure_key=True)
    settings["api_key"] = secrets.token_urlsafe(32)
    clean = {k: settings[k] for k in _default_settings()}
    db.set_state(user_id, SETTINGS_KEY, clean)
    return clean["api_key"]


def _ai_chat_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("DJ AI Base URL is not configured")
    if not base.startswith(("http://", "https://")):
        raise RuntimeError("DJ AI Base URL must start with http:// or https://")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _call_ai(user_id: int, system: str, prompt: str, max_tokens: int = 120) -> str:
    ai = db.get_ai_settings(user_id)
    model = str(ai.get("model") or "").strip()
    if not model:
        raise RuntimeError("DJ AI model is not configured")
    url = _ai_chat_url(str(ai.get("base_url") or ""))
    headers = {"Content-Type": "application/json"}
    key = str(ai.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "temperature": 0.8,
        "max_tokens": max_tokens,
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(40.0, connect=10.0), follow_redirects=True) as client:
            response = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Tasia AI connection failed: {exc}") from exc
    if response.status_code >= 400:
        detail = response.text.strip().replace("\n", " ")[:300]
        raise RuntimeError(f"Tasia AI returned HTTP {response.status_code}: {detail}")
    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(str(x.get("text") or "") if isinstance(x, dict) else str(x) for x in content)
        return str(content or "").strip()
    except Exception as exc:
        raise RuntimeError("Tasia AI returned an unsupported response") from exc


def _clean_spoken(text: str, max_words: int) -> str:
    value = html.unescape(str(text or ""))
    value = re.sub(r"```.*?```", " ", value, flags=re.S)
    value = re.sub(r"[*_`#>]", "", value)
    value = value.replace("\r", " ").replace("\n", " ")
    value = re.sub(r"\s+", " ", value).strip().strip('"“”')
    words = value.split()
    if len(words) > max_words:
        value = " ".join(words[:max_words]).rstrip(" ,;:-") + "."
    if not value:
        raise RuntimeError("Tasia AI returned an empty spoken line")
    return value


def _tts_dir(user_id: int) -> Path:
    path = USER_DATA_DIR / str(int(user_id)) / "tts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cleanup_tts(user_id: int, max_age: float = 86400.0) -> None:
    cutoff = time.time() - max_age
    root = _tts_dir(user_id)
    for path in root.glob("*.mp3"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def _probe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception:
        pass
    return None


def synthesize(user_id: int, text: str, settings: dict[str, Any] | None = None) -> tuple[Path, float | None]:
    settings = settings or get_settings(user_id)
    _cleanup_tts(user_id)
    target = _tts_dir(user_id) / f"tasia-{secrets.token_hex(12)}.mp3"
    cmd = [
        "edge-tts",
        "--voice", str(settings["voice"]),
        "--rate", str(settings["rate"]),
        "--pitch", str(settings["pitch"]),
        "--volume", str(settings["volume"]),
        "--text", text,
        "--write-media", str(target),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Edge TTS failed to start: {exc}") from exc
    if result.returncode != 0 or not target.exists() or target.stat().st_size < 512:
        target.unlink(missing_ok=True)
        detail = (result.stderr or result.stdout or "unknown Edge TTS error").strip()[-400:]
        raise RuntimeError(f"Edge TTS failed: {detail}")
    return target, _probe_duration(target)


def _persona(settings: dict[str, Any], mode: str) -> str:
    custom = str(settings.get("persona_prompt") or "").strip()
    base = custom or DEFAULT_PERSONA
    if mode == "radio":
        return base + " You are live on Tasia Streamer. Produce one short spoken DJ link between songs."
    return base + " You are speaking through Tasia's mesh body in Second Life/OpenSim. Reply directly to the person talking to you."


def radio_line(user_id: int, track: dict[str, Any], settings: dict[str, Any] | None = None) -> str:
    settings = settings or get_settings(user_id)
    title = str(track.get("title") or "this track").strip()
    artist = str(track.get("artist") or "").strip()
    station = str(db.get_stream_settings(user_id).get("name") or "Tasia Radio")
    who = f" by {artist}" if artist else ""
    prompt = (
        f"The song currently playing is {title}{who} on {station}. "
        f"Write what you will say immediately after it ends. Maximum {settings['max_words']} words. "
        "React naturally to the song or the mood. Do not promise what the next song is because you do not know it yet. "
        "Do not begin every link with the same phrase."
    )
    return _clean_spoken(_call_ai(user_id, _persona(settings, "radio"), prompt, 120), int(settings["max_words"]))


def mesh_line(user_id: int, prompt: str, settings: dict[str, Any] | None = None) -> str:
    settings = settings or get_settings(user_id)
    request = (
        f"Someone near your mesh body said: {prompt}\n"
        f"Reply naturally in at most {settings['max_words']} words. No markdown or stage directions."
    )
    return _clean_spoken(_call_ai(user_id, _persona(settings, "mesh"), request, 140), int(settings["max_words"]))


def _prepare_worker(user_id: int, task: PendingTalk, track: dict[str, Any], settings: dict[str, Any]) -> None:
    path: Path | None = None
    try:
        text = radio_line(user_id, track, settings)
        path, duration = synthesize(user_id, text, settings)
        if task.cancelled:
            path.unlink(missing_ok=True)
            return
        task.track = {
            "title": "Tasia Talk",
            "artist": "Tasia",
            "path": str(path),
            "source_type": "tasia-talk",
            "source_url": "",
            "duration": duration,
            "origin": "talk",
            "queue_id": None,
            "library_id": None,
        }
        db.set_state(user_id, LAST_TEXT_KEY, text)
        db.set_state(user_id, LAST_ERROR_KEY, "")
    except Exception as exc:
        task.error = str(exc)[:500]
        db.set_state(user_id, LAST_ERROR_KEY, task.error)
        if path:
            path.unlink(missing_ok=True)
    finally:
        task.event.set()


def schedule_after_track(user_id: int, track: dict[str, Any]) -> None:
    settings = get_settings(user_id, ensure_key=True)
    if not settings.get("enabled") or str(track.get("source_type") or "") == "tasia-talk":
        return
    count = int(db.get_state(user_id, COUNTER_KEY, 0) or 0) + 1
    every = int(settings.get("every_n_tracks") or 1)
    if count < every:
        db.set_state(user_id, COUNTER_KEY, count)
        return
    db.set_state(user_id, COUNTER_KEY, 0)
    task = PendingTalk()
    with _lock:
        old = _pending.get(user_id)
        if old and not old.event.is_set():
            return
        _pending[user_id] = task
    threading.Thread(
        target=_prepare_worker,
        args=(user_id, task, dict(track), settings),
        daemon=True,
        name=f"tasia-talk-{user_id}",
    ).start()


def take_prepared(user_id: int, wait_seconds: float = 18.0) -> dict[str, Any] | None:
    with _lock:
        task = _pending.get(user_id)
    if task is None:
        return None
    task.event.wait(max(0.0, wait_seconds))
    with _lock:
        if _pending.get(user_id) is task:
            _pending.pop(user_id, None)
    if not task.event.is_set():
        task.cancelled = True
        db.set_state(user_id, LAST_ERROR_KEY, "Tasia Talk generation was too slow for the next-song prefetch; this comment was skipped.")
        return None
    return task.track


def clear_pending(user_id: int) -> None:
    with _lock:
        task = _pending.pop(user_id, None)
    if task:
        task.cancelled = True


def _install_next_track_wrapper() -> None:
    if getattr(db.next_track, "_tasia_talk_wrapped", False):
        return
    original = db.next_track

    def next_track_with_talk(user_id: int):
        prepared = take_prepared(user_id)
        if prepared:
            return prepared
        track = original(user_id)
        if track:
            schedule_after_track(user_id, track)
        return track

    next_track_with_talk._tasia_talk_wrapped = True  # type: ignore[attr-defined]
    next_track_with_talk._tasia_talk_original = original  # type: ignore[attr-defined]
    db.next_track = next_track_with_talk  # type: ignore[assignment]


def _cleanup_tokens() -> None:
    now = time.time()
    with _lock:
        for token, (_, _, expires) in list(_audio_tokens.items()):
            if expires <= now:
                _audio_tokens.pop(token, None)
        for job_id, job in list(_mesh_jobs.items()):
            if job.created + 900 <= now:
                _mesh_jobs.pop(job_id, None)


def register_audio(user_id: int, path: Path, ttl: int = 900) -> str:
    _cleanup_tokens()
    token = secrets.token_urlsafe(24)
    with _lock:
        _audio_tokens[token] = (int(user_id), path.resolve(), time.time() + ttl)
    return token


def resolve_audio(token: str) -> Path | None:
    _cleanup_tokens()
    with _lock:
        row = _audio_tokens.get(str(token or ""))
    if not row:
        return None
    _, path, expires = row
    if expires <= time.time() or not path.is_file():
        return None
    return path


def _find_user_for_key(api_key: str, require_mesh: bool = True) -> dict[str, Any] | None:
    needle = str(api_key or "").strip()
    if len(needle) < 16:
        return None
    for user in db.list_users():
        settings = get_settings(int(user["id"]), ensure_key=True)
        if require_mesh and not settings.get("mesh_enabled"):
            continue
        if secrets.compare_digest(str(settings.get("api_key") or ""), needle):
            return user
    return None


def _mesh_worker(job_id: str, user_id: int, prompt: str) -> None:
    with _lock:
        job = _mesh_jobs.get(job_id)
        if job:
            job.status = "working"
    try:
        settings = get_settings(user_id)
        text = mesh_line(user_id, prompt, settings)
        path, duration = synthesize(user_id, text, settings)
        token = register_audio(user_id, path, ttl=900)
        with _lock:
            job = _mesh_jobs.get(job_id)
            if job:
                job.status = "done"
                job.text = text
                job.token = token
                job.duration = duration
        db.set_state(user_id, LAST_TEXT_KEY, text)
        db.set_state(user_id, LAST_ERROR_KEY, "")
    except Exception as exc:
        with _lock:
            job = _mesh_jobs.get(job_id)
            if job:
                job.status = "error"
                job.error = str(exc)[:500]
        db.set_state(user_id, LAST_ERROR_KEY, str(exc)[:500])


def start_mesh_job(user_id: int, prompt: str) -> str:
    _cleanup_tokens()
    job_id = secrets.token_urlsafe(18)
    with _lock:
        _mesh_jobs[job_id] = MeshJob(user_id=int(user_id), created=time.time())
    threading.Thread(target=_mesh_worker, args=(job_id, int(user_id), prompt), daemon=True, name=f"tasia-mesh-{user_id}").start()
    return job_id


def mesh_job(job_id: str, user_id: int) -> MeshJob | None:
    _cleanup_tokens()
    with _lock:
        job = _mesh_jobs.get(str(job_id or ""))
        if not job or job.user_id != int(user_id):
            return None
        return job


def _browser_user(request: Request) -> dict[str, Any]:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.cookies.get("tasia_session")
    if not token:
        raise HTTPException(401, "Login required")
    user = db.session_user(token_hash(token))
    if not user:
        raise HTTPException(401, "Session expired or invalid")
    return user


def _public_base(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}".rstrip("/")


router = APIRouter()


@router.get("/api/settings/tasia-talk")
def api_talk_settings(user: dict = Depends(_browser_user)):
    settings = get_settings(int(user["id"]), ensure_key=True)
    return {"ok": True, **settings}


@router.put("/api/settings/tasia-talk")
def api_talk_settings_save(body: TalkSettingsIn, user: dict = Depends(_browser_user)):
    try:
        settings = save_settings(int(user["id"]), body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **settings}


@router.post("/api/tasia-talk/key/rotate")
def api_talk_key_rotate(user: dict = Depends(_browser_user)):
    return {"ok": True, "api_key": rotate_api_key(int(user["id"]))}


@router.post("/api/tasia-talk/test")
def api_talk_test(request: Request, user: dict = Depends(_browser_user)):
    uid = int(user["id"])
    settings = get_settings(uid)
    prompt = "Give one cheerful microphone test line saying Tasia Talk is ready. Keep it under 18 words."
    try:
        text = _clean_spoken(_call_ai(uid, _persona(settings, "mesh"), prompt, 80), min(18, int(settings["max_words"])))
        path, duration = synthesize(uid, text, settings)
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    token = register_audio(uid, path)
    base = _public_base(request)
    return {
        "ok": True,
        "text": text,
        "duration": duration,
        "audio_url": f"{base}/api/tasia-talk/audio/{token}",
        "media_url": f"{base}/api/tasia-talk/media/{token}",
    }


@router.post("/api/tasia-talk/mesh")
def api_mesh_start(body: MeshRequest):
    user = _find_user_for_key(body.api_key, require_mesh=True)
    if not user:
        raise HTTPException(401, "Invalid or disabled Tasia Talk API key")
    job_id = start_mesh_job(int(user["id"]), body.prompt.strip())
    return {"ok": True, "request_id": job_id, "status": "queued"}


@router.get("/api/tasia-talk/mesh/status/{job_id}")
def api_mesh_status(job_id: str, request: Request, apikey: str = Query(..., min_length=16, max_length=256)):
    user = _find_user_for_key(apikey, require_mesh=True)
    if not user:
        raise HTTPException(401, "Invalid or disabled Tasia Talk API key")
    job = mesh_job(job_id, int(user["id"]))
    if not job:
        raise HTTPException(404, "Tasia Talk request not found or expired")
    payload: dict[str, Any] = {"ok": True, "request_id": job_id, "status": job.status}
    if job.status == "done":
        base = _public_base(request)
        payload.update({
            "text": job.text,
            "duration": job.duration,
            "audio_url": f"{base}/api/tasia-talk/audio/{job.token}",
            "media_url": f"{base}/api/tasia-talk/media/{job.token}",
        })
    elif job.status == "error":
        payload["error"] = job.error or "Tasia Talk failed"
    return payload


@router.get("/api/tasia-talk/audio/{token}")
def api_talk_audio(token: str):
    path = resolve_audio(token)
    if not path:
        raise HTTPException(404, "Tasia Talk audio expired")
    return FileResponse(path, media_type="audio/mpeg", filename="tasia-talk.mp3", headers={"Cache-Control": "no-store"})


@router.get("/api/tasia-talk/media/{token}", response_class=HTMLResponse)
def api_talk_media(token: str):
    if not resolve_audio(token):
        raise HTTPException(404, "Tasia Talk audio expired")
    safe = html.escape(token, quote=True)
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<style>html,body{margin:0;background:#000;color:#fff;font:14px sans-serif}audio{width:100%}</style></head>"
        f"<body><audio autoplay controls src='/api/tasia-talk/audio/{safe}'></audio></body></html>",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/tasia-talk/lsl")
def api_talk_lsl(user: dict = Depends(_browser_user)):
    path = Path(__file__).resolve().parents[1] / "extras" / "TasiaTalkMesh.lsl"
    if not path.exists():
        raise HTTPException(404, "Tasia Talk LSL helper is missing from this build")
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename="TasiaTalkMesh.lsl")


@router.get("/api/tasia-talk/ui.js", response_class=PlainTextResponse)
def api_talk_ui_js():
    path = Path(__file__).resolve().parent / "static" / "tasia-talk.js"
    if not path.exists():
        raise HTTPException(404, "Tasia Talk UI helper is missing")
    return PlainTextResponse(path.read_text("utf-8"), media_type="application/javascript", headers={"Cache-Control": "no-store"})


def _install_fastapi_hook() -> None:
    global _fastapi_hooked
    if _fastapi_hooked:
        return
    _fastapi_hooked = True
    original_init = FastAPI.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        title = str(kwargs.get("title") or "")
        if title != "Tasia Streamer":
            return
        self.include_router(router)

        @self.middleware("http")
        async def tasia_talk_ui_inject(request: Request, call_next):
            if request.url.path in {"/app", "/login"}:
                page = Path(__file__).resolve().parent / "templates" / "index.html"
                if page.exists():
                    text = page.read_text("utf-8")
                    marker = '<script src="/api/tasia-talk/ui.js?v=1"></script>'
                    if marker not in text:
                        text = text.replace("</body>", f"  {marker}\n</body>")
                    return HTMLResponse(text, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})
            return await call_next(request)

        FastAPI.__init__ = original_init

    FastAPI.__init__ = patched_init


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    _install_next_track_wrapper()
    _install_fastapi_hook()
