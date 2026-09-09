from __future__ import annotations

import re
from urllib.parse import quote, urlparse

from . import media
from .config import SUNO_API_BASE, SUNO_API_KEY

# Public Suno playback URLs retained as fallbacks. These are media delivery
# URLs, not generation/account API endpoints, so a public /song/<uuid> can be
# resolved without a Clerk/JWT session.
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

    try:
        uid = media._suno_uuid_from_value(raw)
    except Exception:
        uid = None
    if uid:
        return str(uid).lower()

    match = PUBLIC_M4A_RE.search(raw)
    return match.group(1).lower() if match else None


def _private_suno_url(clip_id: str) -> str | None:
    if not SUNO_API_BASE or not SUNO_API_KEY:
        return None
    return (
        f"{SUNO_API_BASE}/sunoapi/{clip_id}"
        f"?apikey={quote(SUNO_API_KEY, safe='')}"
    )


def _public_candidates(raw: str, clip_id: str) -> list[str]:
    parsed = urlparse(raw)
    out: list[str] = []

    # Preserve an explicit direct media URL first. This matters for signed links.
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if parsed.scheme in {"http", "https"} and (
        (host == "cdn1.suno.ai" and path.endswith(".mp3"))
        or (host == "d2lwuy8qc234o3.cloudfront.net" and path.endswith(".m4a"))
    ):
        out.append(raw)

    # Prefer the configured private downloader when both base URL and key are set.
    private_url = _private_suno_url(clip_id)
    if private_url:
        out.append(private_url)

    # Public fallbacks remain available if the private endpoint is unavailable.
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
    """Prefer a configured private Suno downloader for public clip playback.

    If SUNO_API_BASE and SUNO_API_KEY are configured, normal Suno song URLs and
    bare UUIDs first use:

        {base}/sunoapi/{uuid}?apikey={key}

    Public CloudFront M4A and cdn1 MP3 remain automatic fallbacks. The existing
    authenticated resolver is still kept for unusual short/legacy share links.
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

        return original_resolve(raw, user_id)

    media.resolve_suno_candidates = resolve_suno_candidates
