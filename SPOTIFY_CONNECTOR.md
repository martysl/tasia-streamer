# Tasia Spotify Connector — beta30 candidate

Tasia Streamer beta29 used Spotify's old anonymous web-player token bootstrap for Spotify **text search**. That route is no longer reliable.

This beta30 candidate replaces only that search-token path. Existing Spotify track URL playback remains unchanged and continues through the BTCH resolver/cache pipeline.

## Design

- Spotify track URL → BTCH, no Spotify search token required.
- Spotify text search → the same short-lived Web API token source documented by spotDL's 2026 workaround: the `const token = '...'` example on `developer.spotify.com` while logged into Spotify.
- The Chrome/Chromium connector extracts that token automatically and sends it only to the paired Tasia account.
- Tasia validates every newly captured token against Spotify `/v1/search` before persisting it.
- Spotify search results → private per-user cache for 7 days.
- The token itself is never extended to 7 days. If its expiry cannot be decoded, Tasia conservatively treats it as valid for 55 minutes.
- The connector checks token status every 10 minutes. Tasia marks it refresh-needed with 12 minutes remaining, so normal expiration is refreshed proactively.
- As a fallback, Web Player Bearer values may be observed, but an internal/private token cannot replace a working token unless it passes Tasia's `/v1/search` validation.
- `All Sources` already isolates provider failures, so Spotify token problems do not stop Universal/Audius/other configured sources.

## Server endpoints

- `POST /api/spotify/connector/session`
- `POST /api/spotify/connector/status`
- `GET /api/spotify/connector/download`

The download endpoint creates the current connector ZIP from `extras/tasia-spotify-connector/` on demand.

## Pairing

The beta30 candidate reuses Tasia's existing per-user connector key (currently generated in Settings → Suno connection). This avoids creating another account secret or changing the beta29 settings UI before the browser flow has been verified.

## Browser setup

1. Build/run the beta30 candidate Streamer.
2. Download `/api/spotify/connector/download` from your Tasia Streamer and extract it.
3. Open `chrome://extensions`, enable Developer mode, choose **Load unpacked**, and select `tasia-spotify-connector/`.
4. Enter your Tasia Streamer URL and existing Tasia connector key.
5. Enable **Auto-refresh before the token expires**.
6. Press **Open Spotify token page** and log into Spotify there if necessary.
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

The connector does not receive the Spotify password and does not export the browser cookie jar. It extracts the short-lived token exposed in Spotify's developer-page example and sends it only to the Tasia Streamer origin explicitly approved in the extension.

## Important

This is a compatibility bridge around Spotify's current public developer-page token, not an official developer-app OAuth integration. Spotify can change that page or token flow at any time, so the connector should be verified in a real Chrome/Chromium session before merging beta30.
