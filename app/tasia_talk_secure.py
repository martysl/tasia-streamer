from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import tasia_talk


class MeshStatusRequest(BaseModel):
    api_key: str = Field(min_length=16, max_length=256)


@tasia_talk.router.post("/api/tasia-talk/mesh/status/{job_id}")
def api_mesh_status_secure(job_id: str, body: MeshStatusRequest):
    """Preferred mesh polling route: keep the API key out of the URL/access log."""
    user = tasia_talk._find_user_for_key(body.api_key, require_mesh=True)
    if not user:
        raise HTTPException(401, "Invalid or disabled Tasia Talk API key")
    job = tasia_talk.mesh_job(job_id, int(user["id"]))
    if not job:
        raise HTTPException(404, "Tasia Talk request not found or expired")

    payload: dict[str, Any] = {"ok": True, "request_id": job_id, "status": job.status}
    if job.status == "done":
        payload.update({
            "text": job.text,
            "duration": job.duration,
            "stream_queued": job.token == "stream",
        })
    elif job.status == "error":
        payload["error"] = job.error or "Tasia Talk failed"
    return payload
