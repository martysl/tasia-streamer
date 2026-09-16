from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
import threading

from . import db, tasia_talk

_lock = threading.RLock()
_stream_queue: dict[int, deque[dict]] = defaultdict(deque)
_installed = False


def enqueue_stream_voice(user_id: int, path: Path, text: str, duration: float | None) -> None:
    track = {
        "title": "Tasia Live",
        "artist": "Tasia",
        "path": str(path),
        "source_type": "tasia-talk",
        "source_url": "lsl",
        "duration": duration,
        "origin": "talk",
        "queue_id": None,
        "library_id": None,
    }
    with _lock:
        _stream_queue[int(user_id)].append(track)
    db.set_state(int(user_id), tasia_talk.LAST_TEXT_KEY, text)
    db.set_state(int(user_id), tasia_talk.LAST_ERROR_KEY, "")


def _take_stream_voice(user_id: int) -> dict | None:
    with _lock:
        q = _stream_queue.get(int(user_id))
        if not q:
            return None
        try:
            return q.popleft()
        except IndexError:
            return None


def _mesh_worker_to_stream(job_id: str, user_id: int, prompt: str) -> None:
    with tasia_talk._lock:
        job = tasia_talk._mesh_jobs.get(job_id)
        if job:
            job.status = "working"
    path: Path | None = None
    try:
        settings = tasia_talk.get_settings(user_id)
        text = tasia_talk.mesh_line(user_id, prompt, settings)
        path, duration = tasia_talk.synthesize(user_id, text, settings)
        enqueue_stream_voice(user_id, path, text, duration)
        with tasia_talk._lock:
            job = tasia_talk._mesh_jobs.get(job_id)
            if job:
                job.status = "done"
                job.text = text
                job.duration = duration
                # Marker used by the status API. The audio is not returned to
                # LSL: it is queued for the radio scheduler instead.
                job.token = "stream"
    except Exception as exc:
        if path:
            path.unlink(missing_ok=True)
        with tasia_talk._lock:
            job = tasia_talk._mesh_jobs.get(job_id)
            if job:
                job.status = "error"
                job.error = str(exc)[:500]
        db.set_state(user_id, tasia_talk.LAST_ERROR_KEY, str(exc)[:500])


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True

    # External LSL/mesh speech should be the next available scheduler item.
    # Liquidsoap may already have one ON DECK item prefetched, so we never
    # interrupt the song currently on air; Tasia speaks at the next available
    # transition instead.
    original_next = db.next_track

    def next_track_with_live_tasia(user_id: int):
        live = _take_stream_voice(user_id)
        if live:
            return live
        return original_next(user_id)

    next_track_with_live_tasia._tasia_live_wrapped = True  # type: ignore[attr-defined]
    next_track_with_live_tasia._tasia_live_original = original_next  # type: ignore[attr-defined]
    db.next_track = next_track_with_live_tasia  # type: ignore[assignment]

    # Reuse the existing async job machinery, but route generated audio into
    # Tasia Streamer's playout scheduler instead of returning it as mesh media.
    tasia_talk._mesh_worker = _mesh_worker_to_stream
