# Tasia Spotify Connector — beta30 candidate

Tasia Streamer beta29 used Spotify's old anonymous web-player token bootstrap for Spotify **text search**. That route is no longer reliable.

This beta30 candidate replaces only that search-token path. Existing Spotify track URL playback remains unchanged and continues through the BTCH resolver/cache pipeline.

## Design

- Spotify track URL → BTCH, no Spotify search token required.
- Spotify text search → short-lived Bearer token captured from the user's own logged-in Spotify Web Player.
- Spotify search results → private per-user cache for 7 days.
- The token itself is never extended to 7 days. If its expiry cannot be decoded, Tasia conservatively treats it as valid for 55 minutes.
- The Chrome/Chromium connector checks token status periodically and refreshes before expiry by reloading an inactive Spotify tab or opening a temporary inactive helper tab.
- `All Sources` already isolates provider failures, so Spotify token problems do not stop Universal/Audius/other configured sources.

## Server endpoints

- `POST /api/spotify/connector/session`
- `POST /api/spotify/connector/status`
- `GET /api/spotify/connector/download`

The download endpoint creates the current connector ZIP from `extras/tasia-spotify-connector/` on demand.

## Pairing

The beta30 candidate reuses Tasia's existing per-user connector key (currently generated in Settings → Suno connection). This avoids creating another account secret or changing the beta29 settings UI before the browser flow has been verified.

## Browser setup

1. Keep your own account logged into `https://open.spotify.com/`.
2. Download `/api/spotify/connector/download` from your Tasia Streamer.
3. Extract the ZIP.
4. Open `chrome://extensions`, enable Developer mode, choose **Load unpacked**, and select `tasia-spotify-connector/`.
5. Enter your Tasia Streamer URL and existing Tasia connector key.
6. Enable **Auto-refresh before the token expires**.
7. Press **Refresh token now** once.
8. Test Spotify text search in Online / Universal.

## Storage and privacy

Server-side token:

```text
/data/users/<user-id>/secrets/spotify-token.json
```

Search cache:

```text
/data/users/<user-id>/spotify-search-cache.json
```

The connector does not read the Spotify password or export the browser cookie jar. It observes the short-lived Bearer token used by the logged-in Spotify web player and sends it only to the Tasia Streamer origin explicitly approved in the extension.

## Important

This is a browser-session compatibility bridge, not an official Spotify developer-app authentication flow. Spotify can change its web player at any time, so the connector should be verified in a real Chrome/Chromium session before merging beta30.
