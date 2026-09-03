from __future__ import annotations

import re
from urllib.parse import urlparse

from . import media

# Current Suno playback URLs exposed by the web player and used by the
# suno-free-download extension.  These are media delivery URLs, not generation
# or account API endpoints, so a public /song/<uuid> can be resolved without a
# Clerk/JWT session.
PUBLIC_M4A_TEMPLATE = "https://d2lwuy8qc234o3.cloudfront.net/1/clip/{clip_id}.m4a"
PUBLIC_MP3_TEMPLATE = "https://cdn1.suno.ai/{clip_id}.mp3"
PUBLIC_M4A_RE = re.compile(
    r"https?://d2lwuy8qc234o3\.cloudfront\.net/1/clip/([0-9a-fA-F-]{36})\.m4a(?:\?[^\s]*)?",
    re.I,
)

_installed = False


def _clip_id(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None

    # Keep the parser from media.py for bare UUIDs, suno.com/song/<uuid>, and
    # cdn1.suno.ai/<uuid>.mp3, then add the progressive M4A form discovered in
    # the extension.
    try:
        uid = media._suno_uuid_from_value(raw)
    except Exception:
        uid = None
    if uid:
        return str(uid).lower()

    match = PUBLIC_M4A_RE.search(raw)
    return match.group(1).lower() if match else None


def _public_candidates(raw: str, clip_id: str) -> list[str]:
    parsed = urlparse(raw)
    out: list[str] = []

    # Preserve an explicit direct media URL first. This matters for signed
    # links, while still giving us stable public fallbacks afterwards.
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if parsed.scheme in {"http", "https"} and (
        (host == "cdn1.suno.ai" and path.endswith(".mp3"))
        or (host == "d2lwuy8qc234o3.cloudfront.net" and path.endswith(".m4a"))
    ):
        out.append(raw)

    out.extend(
        [
            PUBLIC_M4A_TEMPLATE.format(clip_id=clip_id),
            PUBLIC_MP3_TEMPLATE.format(clip_id=clip_id),
        ]
    )

    unique: list[str] = []
    seen: set[str] = set()
    for url in out:
        if url and url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def install() -> None:
    """Make public Suno clip playback the default resolver.

    The old authenticated API/session machinery remains import-compatible for
    unusual legacy/share links, but normal song URLs and UUIDs no longer require
    a Suno login. cache_remote_audio() already understands multiple candidate
    URLs and will validate/transcode the first playable one with FFmpeg.
    """

    global _installed
    if _installed:
        return
    _installed = True

    original_resolve = media.resolve_suno_candidates

    def resolve_suno_candidates(value: str, user_id: int | None = None):
        raw = str(value or "").strip()
        uid = _clip_id(raw)
        if uid:
            return _public_candidates(raw, uid), uid

        # Non-Suno URLs and old share forms without a visible UUID keep the
        # existing resolver as a compatibility fallback.
        return original_resolve(raw, user_id)

    media.resolve_suno_candidates = resolve_suno_candidates
