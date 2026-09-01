Tasia Spotify Connector v1.1
============================

Purpose
-------
Keeps Spotify TEXT search working in Tasia Streamer without requiring a Spotify
Developer app / Client ID / Client Secret.

Spotify track URLs do not need this connector. They still resolve through BTCH.

How it works
------------
1. Spotify's 2026 spotDL workaround uses the short-lived token shown in the code
   example on https://developer.spotify.com/ after you log into Spotify.
2. Tasia Spotify Connector opens/reloads that page when a fresh token is needed
   and reads the `const token = '...'` value from the rendered code example.
3. The token is sent only to the Tasia Streamer URL you explicitly approve.
4. Tasia validates it against Spotify /v1/search before storing it.
5. Search results are cached privately for 7 days. The token itself is NOT made
   valid for 7 days; it remains short-lived (normally about one hour).
6. Every 10 minutes the connector asks Tasia whether refresh is needed. Tasia
   asks for refresh with 12 minutes of token life remaining, so normal expiry is
   refreshed proactively.
7. As a fallback, the connector can observe Bearer values used by the normal
   Spotify web player. They are never stored unless Tasia validates them first.

Setup
-----
1. Build/run the beta30 candidate Streamer.
2. Download:
      /api/spotify/connector/download
3. Extract the ZIP.
4. Chrome/Chromium -> chrome://extensions
5. Enable Developer mode.
6. Load unpacked -> choose the tasia-spotify-connector folder.
7. In Tasia Streamer Settings -> Suno connection, generate/copy the existing
   Connector key. Beta30 reuses that per-user Tasia pairing key for Spotify too.
8. In the extension enter your Streamer URL + connector key and press Save.
9. Press Open Spotify token page. Log into Spotify there if needed.
10. Press Refresh token now. Once the token is validated, Spotify text search is
    ready and future refreshes should happen automatically.

Security
--------
- The connector does not receive your Spotify password.
- It does not export your full browser cookie jar.
- It extracts only the short-lived token exposed by Spotify's developer page,
  plus a web-player Bearer fallback which must pass backend validation.
- It sends the token only to the Streamer origin you approve through Chrome's
  optional host permission prompt.
- Server-side token files are mode 0600 when the platform supports it.
- Treat the Tasia connector key like a password.

Troubleshooting
---------------
If Tasia says a fresh Spotify token is required:
- open https://developer.spotify.com/ and make sure you are logged in;
- confirm the page contains a code example with `const token = '...'`;
- click Refresh token now;
- use Sync now after a server/container restart if the browser token is still valid.

The connector is intentionally for Spotify catalog metadata/search only. Playback
of selected Spotify links remains handled by Tasia's BTCH resolver/cache pipeline.
