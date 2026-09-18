from __future__ import annotations

from pathlib import Path

from . import db, engine, tasia_talk

_installed = False


def enqueue_stream_voice(user_id: int, path: Path, text: str, duration: float | None) -> None:
    """Push an LSL/mesh voice line straight into Liquidsoap's live mic queue.

    The Liquidsoap graph mixes this queue over the currently playing music and
    ducks the music while a live voice request is ready/playing. It never waits
    for the current song to end.
    """
    uid = int(user_id)

    # A live SL/OpenSim interaction is more important than an automatically
    # prepared between-song comment. Drop the pending automatic link so Tasia
    # does not speak twice back-to-back.
    tasia_talk.clear_pending(uid)

    engine.push_live_voice(uid, path)

    db.set_state(uid, tasia_talk.LAST_TEXT_KEY, text)
    db.set_state(uid, tasia_talk.LAST_ERROR_KEY, "")


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

        # Push first so "done" really means the live radio accepted the line.
        enqueue_stream_voice(user_id, path, text, duration)

        # Keep the normal status/audio URL useful to LSL and the web UI too.
        token = tasia_talk.register_audio(user_id, path, ttl=900)

        with tasia_talk._lock:
            job = tasia_talk._mesh_jobs.get(job_id)
            if job:
                job.status = "done"
                job.text = text
                job.duration = duration
                job.token = token
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

    # Reuse the existing async AI/TTS job machinery, but once the MP3 is ready
    # inject it immediately into Liquidsoap's live voice overlay. Normal
    # automatic Tasia Talk still uses the between-song scheduler path.
    tasia_talk._mesh_worker = _mesh_worker_to_stream
