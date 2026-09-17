from __future__ import annotations

from urllib.parse import urlparse

import httpx

_INSTALLED = False
_ORIGINAL_POST = httpx.Client.post


def install() -> None:
    """Make Pawan.Krd API calls look like normal browser-originated API traffic.

    Pawan's Cloudflare/WAF currently accepts the same authenticated request from
    curl when a normal browser User-Agent is present, but can return an HTML
    `Access Denied` page for httpx's default Python fingerprint.  Keep this fix
    narrowly scoped to api.pawan.krd so other providers keep their normal httpx
    request behaviour.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    def post(self: httpx.Client, url, *args, **kwargs):
        try:
            host = (urlparse(str(url)).hostname or "").lower()
        except Exception:
            host = ""

        if host == "api.pawan.krd" or host.endswith(".api.pawan.krd"):
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("Accept", "application/json")
            headers.setdefault(
                "User-Agent",
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36",
            )
            kwargs["headers"] = headers

        return _ORIGINAL_POST(self, url, *args, **kwargs)

    httpx.Client.post = post
