from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app import spotify_bridge


def test_cache_roundtrip_and_expiry(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(spotify_bridge, "USER_DATA_DIR", tmp_path)
    rows = [{"provider": "btch-spotify", "id": "abc", "title": "Song", "artist": "Artist"}]
    spotify_bridge._cache_put(1, " Artist   - Song ", rows)
    assert spotify_bridge._cache_get(1, "artist - song", 10) == rows

    cache_path = tmp_path / "1" / "spotify-search-cache.json"
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["entries"]["artist - song"]["created_at"] = time.time() - spotify_bridge.SEARCH_CACHE_SECONDS - 1
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    assert spotify_bridge._cache_get(1, "artist - song", 10) is None


def test_spotify_url_keeps_original_btch_path(monkeypatch):
    called = {}

    def original(provider, settings, query, limit=30):
        called.update(provider=provider, settings=settings, query=query, limit=limit)
        return [{"ok": True}]

    monkeypatch.setattr(spotify_bridge, "_original_search", original)
    result = spotify_bridge._search_wrapper(
        "btch-spotify",
        {"user_id": 1},
        "https://open.spotify.com/track/123",
        7,
    )
    assert result == [{"ok": True}]
    assert called["provider"] == "btch-spotify"
    assert called["query"].startswith("https://open.spotify.com/")


def test_spotify_text_search_uses_bridge(monkeypatch):
    called = {}

    def bridge(settings, query, limit):
        called.update(settings=settings, query=query, limit=limit)
        return [{"provider": "btch-spotify", "title": "Found"}]

    monkeypatch.setattr(spotify_bridge, "_spotify_search_with_bridge", bridge)
    result = spotify_bridge._search_wrapper("btch-spotify", {"user_id": 9}, "Daft Punk", 5)
    assert result[0]["title"] == "Found"
    assert called == {"settings": {"user_id": 9}, "query": "Daft Punk", "limit": 5}


def test_missing_token_gives_useful_error(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(spotify_bridge, "USER_DATA_DIR", tmp_path)
    with pytest.raises(spotify_bridge.catalogs.CatalogError) as exc:
        spotify_bridge._spotify_search_with_bridge({"user_id": 1}, "song", 5)
    text = str(exc.value)
    assert "fresh browser token" in text
    assert "BTCH" in text
