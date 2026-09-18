from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from .config import AUDIO_EXTENSIONS, MAX_REMOTE_BYTES, USER_DATA_DIR
from .media import ffprobe

EXT_DIR = Path("/app/spotiflac-extensions")
DEFAULT_REGISTRY = (
    "https://raw.githubusercontent.com/spotiflacapp/"
    "SpotiFLAC-Extension/main/registry.json"
)
DEFAULT_SERVICES = (
    "ext:tidal-web",
    "ext:qobuz-web",
    "ext:amazon",
    "ext:deezer",
)
ALLOWED_SERVICES = set(DEFAULT_SERVICES)

_locks_guard = threading.RLock()
_locks: dict[str, threading.Lock] = {}


def _services() -> list[str]:
    raw = str(os.getenv("SPOTIFLAC_SERVICES", "") or "").strip()
    wanted = [x.strip() for x in raw.split(",") if x.strip()] if raw else list(DEFAULT_SERVICES)
    result: list[str] = []
    for value in wanted:
        service = value if value.lower().startswith("ext:") else f"ext:{value}"
        if service not in ALLOWED_SERVICES:
            raise RuntimeError(
                f"SpotiFLAC service '{service}' is not enabled for Spotify audio. "
                "Tasia keeps YouTube on the explicit Universal/API path only."
            )
        if service not in result:
            result.append(service)
    if not result:
        raise RuntimeError("No SpotiFLAC lossless services are configured")
    return result


def _canonical_spotify_url(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in {"open.spotify.com", "www.open.spotify.com"}:
        raise ValueError("SpotiFLAC needs an open.spotify.com track URL")
    parts = [x for x in parsed.path.split("/") if x]
    if len(parts) < 2 or parts[0].lower() != "track" or not parts[1]:
        raise ValueError("SpotiFLAC currently expects a Spotify track URL")
    return f"https://open.spotify.com/track/{parts[1]}"


def _cache_root(user_id: int) -> Path:
    root = USER_DATA_DIR / str(int(user_id)) / "cache" / "spotiflac"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def _patch_extension_dir() -> None:
    try:
        from SpotiFLAC.extensions import manager as ext_manager
    except Exception as exc:
        raise RuntimeError(f"SpotiFLAC extension manager could not load: {exc}") from exc
    ext_manager.DEFAULT_EXT_DIR = EXT_DIR


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


def _valid_audio(path: Path) -> tuple[bool, float | None]:
    if not path.is_file() or path.stat().st_size <= 0:
        return False, None
    if path.stat().st_size > MAX_REMOTE_BYTES:
        return False, None
    duration, ok = ffprobe(path)
    return ok, duration


async def _download(url: str, root: Path, stem: str, services: list[str]) -> None:
    _patch_extension_dir()
    try:
        from SpotiFLAC import AsyncSpotiFLAC
    except Exception as exc:
        raise RuntimeError(f"SpotiFLAC could not load: {type(exc).__name__}: {exc}") from exc

    async with AsyncSpotiFLAC(
        output_dir=str(root),
        services=services,
        filename_format=stem,
        quality="LOSSLESS",
        allow_fallback=True,
        embed_lyrics=False,
        enrich_metadata=False,
        save_canvas=False,
        transcode_to="flac",
        transcode_keep_original=False,
        timeout_s=120,
        max_concurrent_downloads=1,
        sync_extensions=False,
    ) as client:
        failed = await client.download_track(url)
        if failed:
            names = ", ".join(str(getattr(x, "title", "") or "track") for x in failed[:3])
            raise RuntimeError(f"SpotiFLAC could not resolve/download: {names}")


def cache_spotify_track(spotify_url: str, user_id: int) -> tuple[Path, float | None, None]:
    url = _canonical_spotify_url(spotify_url)
    services = _services()
    root = _cache_root(user_id)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    final = root / f"{digest}.flac"

    ok, duration = _valid_audio(final)
    if ok:
        return final, duration, None

    with _lock_for(f"{user_id}:{digest}"):
        ok, duration = _valid_audio(final)
        if ok:
            return final, duration, None

        for stale in root.glob(f"{digest}.*"):
            if stale != final and stale.suffix.lower() in AUDIO_EXTENSIONS:
                try:
                    stale.unlink()
                except OSError:
                    pass

        _run_async(_download(url, root, digest, services))

        ok, duration = _valid_audio(final)
        if not ok:
            # Defensive fallback in case an extension ignored the requested
            # transcode destination but still produced valid audio.
            candidates = sorted(
                (
                    p for p in root.glob(f"{digest}.*")
                    if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
                ),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            source = next((p for p in candidates if _valid_audio(p)[0]), None)
            if source is None:
                raise RuntimeError("SpotiFLAC finished without a playable audio file")
            tmp = final.with_suffix(".tmp.flac")
            tmp.unlink(missing_ok=True)
            result = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(source), "-vn", "-codec:a", "flac", str(tmp),
                ],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if result.returncode != 0 or not tmp.is_file():
                tmp.unlink(missing_ok=True)
                raise RuntimeError(
                    "FFmpeg could not normalize SpotiFLAC audio: "
                    + (result.stderr or result.stdout or "unknown error").strip()[-300:]
                )
            os.replace(tmp, final)
            if source != final:
                try:
                    source.unlink()
                except OSError:
                    pass
            ok, duration = _valid_audio(final)

        if not ok:
            final.unlink(missing_ok=True)
            raise RuntimeError("SpotiFLAC produced invalid or oversized audio")

        return final, duration, None


def runtime_status() -> dict:
    _patch_extension_dir()
    try:
        version = importlib.metadata.version("SpotiFLAC")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("SpotiFLAC is not installed in the streamer image") from exc

    missing = []
    for service in _services():
        ext_id = service[4:]
        if not (EXT_DIR / ext_id / "manifest.json").is_file():
            missing.append(ext_id)
    if missing:
        raise RuntimeError("Missing SpotiFLAC extension(s): " + ", ".join(missing))
    return {
        "ok": True,
        "message": (
            f"SpotiFLAC {version} ready with lossless providers: "
            + ", ".join(x[4:] for x in _services())
            + ". YouTube remains explicit through Tasia Universal/API only."
        ),
    }
