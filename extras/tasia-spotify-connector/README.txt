Tasia Spotify Connector v1.0
============================

Purpose
-------
Keeps Spotify TEXT search working in Tasia Streamer without requiring a Spotify
Developer app / Client ID / Client Secret.

Spotify track URLs do not need this connector. They still resolve through BTCH.

How it works
------------
1. Stay logged into your own account in https://open.spotify.com/.
2. The extension observes only Authorization headers sent by the Spotify web
   player to https://api.spotify.com/.
3. When Spotify issues a fresh short-lived Bearer token, the extension sends that
   token only to the Tasia Streamer URL that you explicitly approve.
4. Tasia validates it with Spotify search, stores it privately under the current
   Tasia account, and uses it only for Spotify catalog text search.
5. Search results are cached for 7 days. The token itself is NOT extended to 7
   days; it remains short-lived and is refreshed when needed.
6. Every 10 minutes the connector asks Tasia whether the token needs refreshing.
   If needed it refreshes an existing inactive Spotify tab, or opens a temporary
   inactive helper tab. A helper tab created by the connector closes after a new
   token is captured and synced.

Setup
-----
1. Chrome/Chromium -> chrome://extensions
2. Enable Developer mode.
3. Load unpacked -> choose this tasia-spotify-connector folder.
4. In Tasia Streamer Settings -> Suno connection, generate/copy the existing
   Connector key. Beta30 reuses that per-user Tasia pairing key for Spotify too.
5. In the extension enter:
      Tasia Streamer URL: https://your-streamer.example
      Connector key:     <your Tasia connector key>
6. Press Save, then Refresh token now.
7. Keep your Spotify account logged in. You do not need to keep a Spotify tab
   permanently open when Auto-refresh is enabled.

Security
--------
- The connector does not read your Spotify password or full cookie jar.
- It forwards only the short-lived Spotify Bearer token seen on Spotify API
  requests.
- The token is sent only to the Streamer origin you approve through Chrome's
  optional host permission prompt.
- Server-side token files are written mode 0600 when the platform supports it.
- Treat the Tasia connector key like a password.

Troubleshooting
---------------
If Tasia says a fresh Spotify token is required:
- confirm you are logged into open.spotify.com;
- click Refresh token now in the extension;
- if Spotify shows a login page, log in and let the web player finish loading;
- use Sync now after a server/container restart if the browser token is still valid.

The connector is intentionally for catalog metadata/search only. Playback of
selected Spotify links remains handled by Tasia's BTCH resolver/cache pipeline.
