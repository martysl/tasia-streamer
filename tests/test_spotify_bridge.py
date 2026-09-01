from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import spotify_bridge


class SpotifyBridgeTests(unittest.TestCase):
    def test_cache_roundtrip_and_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [{"provider": "btch-spotify", "id": "abc", "title": "Song", "artist": "Artist"}]
            with patch.object(spotify_bridge, "USER_DATA_DIR", root):
                spotify_bridge._cache_put(1, " Artist   - Song ", rows)
                self.assertEqual(spotify_bridge._cache_get(1, "artist - song", 10), rows)

                cache_path = root / "1" / "spotify-search-cache.json"
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                payload["entries"]["artist - song"]["created_at"] = time.time() - spotify_bridge.SEARCH_CACHE_SECONDS - 1
                cache_path.write_text(json.dumps(payload), encoding="utf-8")
                self.assertIsNone(spotify_bridge._cache_get(1, "artist - song", 10))

    def test_spotify_url_keeps_original_btch_path(self):
        called = {}

        def original(provider, settings, query, limit=30):
            called.update(provider=provider, settings=settings, query=query, limit=limit)
            return [{"ok": True}]

        with patch.object(spotify_bridge, "_original_search", original):
            result = spotify_bridge._search_wrapper(
                "btch-spotify",
                {"user_id": 1},
                "https://open.spotify.com/track/123",
                7,
            )
        self.assertEqual(result, [{"ok": True}])
        self.assertEqual(called["provider"], "btch-spotify")
        self.assertTrue(called["query"].startswith("https://open.spotify.com/"))

    def test_spotify_text_search_uses_bridge(self):
        called = {}

        def bridge(settings, query, limit):
            called.update(settings=settings, query=query, limit=limit)
            return [{"provider": "btch-spotify", "title": "Found"}]

        with patch.object(spotify_bridge, "_spotify_search_with_bridge", bridge):
            result = spotify_bridge._search_wrapper("btch-spotify", {"user_id": 9}, "Daft Punk", 5)
        self.assertEqual(result[0]["title"], "Found")
        self.assertEqual(called, {"settings": {"user_id": 9}, "query": "Daft Punk", "limit": 5})

    def test_missing_token_gives_useful_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(spotify_bridge, "USER_DATA_DIR", Path(tmp)):
                with self.assertRaises(spotify_bridge.catalogs.CatalogError) as ctx:
                    spotify_bridge._spotify_search_with_bridge({"user_id": 1}, "song", 5)
        text = str(ctx.exception)
        self.assertIn("fresh browser token", text)
        self.assertIn("BTCH", text)


if __name__ == "__main__":
    unittest.main()
