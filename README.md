# Tasia Streamer 2.0 beta29

A compact multi-user SHOUTcast DJ workstation built around FastAPI + SQLite + FFmpeg + Liquidsoap 2.4.5.

Beta28 keeps the full beta27 Suno stack and adds btch-downloader URL providers for Spotify, SoundCloud and Google Drive.

## Multi-source search and song-list import (beta29)

The Online tab now has **All Sources**, which searches Spotify, Universal Search, Audius and any configured SoundCloud, Jamendo and Stremio sources in parallel. Matching versions from different services stay visible so you can choose the provider.

Import TXT can use a file or a pasted list. Put one song or supported URL on each line. **Find songs in** can be Auto/All or a specific service. Auto checks the private local library for an exact match first, then searches online. Spotify/SoundCloud/Google Drive/YouTube/Suno URLs are detected automatically in mixed lists.


## BTCH URL providers (beta28)

Online / Universal now includes Spotify (BTCH), SoundCloud (BTCH), and Google Drive (BTCH). Paste a source URL, resolve it, then use Q / P / Saved like the other catalog providers. Queueing caches and validates a private MP3 copy before Liquidsoap uses it. The Docker image installs `btch-downloader@6.3.6` with Node.js.

## Suno (beta27)

Suno uses a refreshable browser-session bridge. Install the bundled **Tasia Suno Connector v1.1**, pair it with the per-user connector key, and keep your own browser logged into suno.com. The connector forwards only Suno's Clerk `__client` session value and device ID to the Tasia Streamer URL you explicitly approve. Docker exchanges that session for fresh JWTs via Clerk and can refresh them without running a browser inside the container.

Track playback resolves the UUID through Suno's authenticated feed API and caches the returned `audio_url` locally before Liquidsoap sees it. Tasia no longer constructs raw `cdn1.suno.ai/<uuid>.mp3` URLs.

A manual `__client` paste is available in Settings for headless/manual setups. Treat that value like a password. Legacy Bearer and signed-cookie modes remain only for compatibility.

## What's new in v2

- **Suno via refreshable browser session/API.** Paste a `suno.com/song/<uuid>` URL, short share link or bare UUID. The bundled Tasia Suno Connector passes your own Clerk `__client` session + device ID after explicit pairing; Docker refreshes JWTs itself, resolves the clip through Suno's feed API, takes the returned `audio_url`, and caches it privately before playout.
- **SAM-style one-screen layout:** Library/Sources | On-Air + Queue | Playlist + Suno.
- **Real folder browser:** local folders and subfolders appear directly in the song chooser. Open a folder like a file manager, or add the whole folder tree to Queue/Playlist in one click.
- **Fast global search:** search the current user's full private library by title, artist or path without leaving the live workstation.
- **Optional DJ AI adviser:** each user can configure their own OpenAI-compatible Base URL, API key and model. The adviser receives the current set/library context and gives suggestions only; it never changes playback automatically.
- **Drag & drop** ordering in both Queue and Playlist. The one Liquidsoap-preloaded `ON DECK` item is locked because it is already committed to the engine.
- **Set timing:** queue duration, set-end clock, per-track queue ETA, playlist cumulative offset and "if started now" clock.
- **Private folder-aware local library** for every account under `./music/users/<id>-<username>/`, including all subfolders.
- **WebDAV + FTP/FTPS + Jellyfin browser** with folders and remote search. Jellyfin uses normal per-user username/password login; selected tracks are cached locally before Queue/Playlist playout.
- **Online catalogs:** SoundCloud, Audius, Jamendo and direct-stream Stremio addons can be searched from the workstation and added to Queue/Playlist. Their playable URLs are resolved when the track is due.
- **Multi-user login:** each account has its own library index, uploads/cache, queue, playlist, remote sources, settings and SHOUTcast profile.
- **Independent radio engines:** each logged-in account can run its own Liquidsoap process, so profiles are not just cosmetic wrappers around one global queue.
- Existing v1.x `library`, `queue`, and `playlist` tables are left untouched. The **first v2 account copies/adopts them automatically**.

## Upgrade from v1.x

Keep your existing:

```text
.env
music/
data/
```

Extract v2 over the `tasia-streamer` directory, then run:

```bash
cd tasia-streamer
./upgrade-v2.sh
```

The old `ADMIN_TOKEN` is no longer the web login. On first visit v2 asks you to create the first username/password. That first account becomes admin and adopts the old v1.x library/queue/playlist.

Your old SHOUTcast values in `.env` are used as the **default stream profile for that first account**, so you do not need to type them again immediately.

## Fresh install

```bash
cp .env.example .env
nano .env
docker compose up -d --build
```

Open:

```text
http://SERVER-IP:8787
```

Routes:

```text
/       public landing page
/login  login / first-account setup
/app    DJ workstation
```

The first browser visit can use **Launch your station** on the landing page to create the administrator account. Public self-registration is disabled by default; the admin can create additional accounts from **Settings**. Set `ALLOW_REGISTRATION=true` only if you deliberately want a public registration API.

## Local library

Each account gets its own folder automatically:

```text
./music/users/<id>-<username>/
```

Put that user's songs and folders only inside that directory, then press **Scan**. Subfolders are preserved in the song chooser and are not visible to other accounts. Supported formats:

```text
MP3 WAV FLAC OGG OGA OPUS M4A AAC WMA
```

Browser uploads go to that user's private `Uploads/` folder under `./music/users/<id>-<username>/`. Remote/CDN caches and engine data stay private under:

```text
./data/users/<user-id>/
```

## Suno

Recommended setup:

1. Open **Settings → Suno connection** and generate a connector key.
2. Download/install the bundled **Tasia Suno Connector v1.1** in Chrome/Chromium.
3. Enter your Tasia Streamer URL + connector key in the extension.
4. Stay logged into your own account at `suno.com`, then press **Save & Sync**.

The extension reads only Suno's Clerk `__client` session value and device ID after pairing. It does **not** capture request Bearer headers or forward your whole browser cookie jar. Tasia stores the session server-side, exchanges it with Clerk for a JWT, and refreshes the JWT automatically when it is stale.

Accepted track inputs include:

```text
https://suno.com/song/453a796e-a8e2-4d28-b24f-40f956cb5321
https://suno.com/s/SHORT_SHARE_ID
453a796e-a8e2-4d28-b24f-40f956cb5321
```

For a UUID/song link Tasia asks Suno's authenticated `/api/feed/?ids=<uuid>` endpoint for the clip and uses the API-returned `audio_url`. The audio is cached locally before Liquidsoap sees it. Tasia does not construct `cdn1.suno.ai/<uuid>.mp3` from the UUID.

If Suno returns `401` or an auth-stale response, Tasia refreshes the JWT from the saved Clerk session and retries the API request once. A **Refresh now** button is also available in Settings.

For a headless/manual setup you can paste only your own `__client` value (or a Cookie header containing `__client=...`) in Settings. Treat it like a password. Legacy raw-Bearer and signed-cookie support remains only for compatibility with older beta installs.

## DJ AI adviser

DJ AI is optional. In **Settings** configure:

```text
Base URL: http://your-model-server:1234/v1
API key:  optional for local servers
Model:     your-model-name
```

The app calls the OpenAI-compatible `POST /v1/chat/completions` interface. If your Base URL already ends in `/v1`, Tasia appends `/chat/completions`; if you provide the full `/chat/completions` URL it is used as-is.

The API key is stored server-side in the local SQLite database and is never returned through the settings API or embedded in browser JavaScript. Like the SHOUTcast/WebDAV passwords, protect the `./data` directory.

Press **DJ AI** in the top bar to ask for next-track ideas, set sequencing, or party rescue suggestions. It receives current On Air/Queue/Playlist context plus folder summaries and a bounded slice of the current library view. Advice is read-only and does not alter the queue.

## WebDAV / FTP / Jellyfin

Open **Remote / Jellyfin** in the left song chooser, add a source, use **Test connection**, then save and browse folders. WebDAV automatically negotiates Basic or Digest authentication; FTP and FTPS use the username/password fields (or credentials embedded in the URL). Remote files are downloaded into the current user's cache only when you add them to Queue or Playlist, so a temporary WebDAV/FTP/Jellyfin outage does not interrupt a live song halfway through.

Examples:

```text
WebDAV: https://cloud.example.com/remote.php/dav/files/mom/Music/
FTP:    ftp://ftp.example.com/music/
FTPS:   ftps://ftp.example.com/music/
Jellyfin: https://jellyfin.example.com
```

For Jellyfin, enter the normal Jellyfin **username and password**. Tasia authenticates through Jellyfin's user login, keeps the returned access token internal, and browses only the music libraries available to that Jellyfin account. Jellyfin tracks are streamed into the current Tasia user's private cache when you add them to Queue or Playlist.

Remote-source and SHOUTcast passwords are stored in the local SQLite database. Protect the `./data` directory like any other application secrets directory.

## Online streaming catalogs

The **Online catalogs** tab restores the provider integrations alongside Jellyfin:

- **SoundCloud** — requires a registered SoundCloud application Client ID + Client Secret. Tasia uses server-side Client Credentials for public search and resolves the current AAC/HLS URL from `/tracks/{track_urn}/streams` when the queued item is due.
- **Audius** — public read-only search and streaming work without credentials; an optional backend Bearer Token can be stored per Tasia account.
- **Jamendo** — requires a Jamendo API Client ID. Search results expose available license information and use the API-provided audio stream URL.
- **Stremio Addon** — save an addon `manifest.json` URL. Tasia reads its catalogs/search support and accepts only results resolving to direct `http://` or `https://` `stream.url` values. Torrent/infoHash, magnet, YouTube-ID, external-player and DRM-style transports are not sent to the radio engine.

Catalog credentials and manifest URLs are per Tasia user. Secrets are stored server-side and are redacted from browser settings responses.

## DJ controls

- **Connect / Disconnect** controls the current account's SHOUTcast source connection.
- **Play / Pause / Stop / Skip** controls playout.
- Pause switches to silence and freezes the progress clock.
- Queue ETA is shown only while the source is genuinely connected and playing.

## Multi-user model

Every account owns separate v2 tables/rows for:

- Library index
- Queue
- Playlist
- On-air state/progress
- WebDAV/FTP/Jellyfin source definitions
- SoundCloud/Audius/Jamendo/Stremio catalog settings
- SHOUTcast host/password/SID/name/genre/bitrate/sample rate
- Autoplay and auto-connect settings

`./music` is only the Docker mount. The application does **not** expose it as one shared library: each account is restricted to its own `/music/users/<id>-<username>/` subtree. Remote caches are also private per account.

## Useful commands

```bash
docker compose up -d --build
docker compose logs -f tasia-streamer
docker compose restart
docker compose down
```

## Notes

- Liquidsoap telnet and metadata ports are bound inside the container only and are not published by Docker Compose.
- Each user engine gets its own internal control/metadata ports.
- Direct HTTP/Suno and WebDAV/FTP/Jellyfin audio is cached/validated before playout. Online catalog tracks keep provider IDs and resolve their stream URL only when due.
- `ALLOW_PRIVATE_URLS=false` applies to direct HTTP/Suno and Stremio addon/stream URLs. WebDAV/FTP/Jellyfin sources are explicit authenticated user configuration and may point to a LAN/NAS.

## Private user music folders
Each account owns one local folder under `/music/users/<id>-<username>/`. Put folders and songs anywhere under that root and press **Scan**. Other accounts cannot browse or queue those files. Uploaded files are stored in that account's `Uploads/` subfolder.

When upgrading from beta1/v1, existing top-level `/music` content is moved once into the first account's private folder. Beta3 also repairs stale beta2 database paths on startup so queued songs continue to point at the moved files.


### Universal Search + MP3 converter (beta10)

The Online / Universal chooser can search by song/artist text or accept a YouTube/Spotify link. yt-dlp is used only to discover YouTube results. When Q/P is pressed, Tasia sends the chosen YouTube URL to the per-user MP3 converter API (default `https://yapi.is-on.click/api/convert`), streams the returned audio into the private user cache, validates/normalizes it with FFmpeg, then inserts the local MP3 into Queue/Playlist. Spotify links use Spotify oEmbed for basic title metadata only; Spotify is not used as the audio transport. Optional Netscape-format YouTube search `cookies.txt` can be uploaded in Settings and is stored per user under `/data/users/<id>/secrets/`.


### TXT set import
Use **Import TXT** in the Playlist panel. One song or link per line; blank lines and `# comments` are ignored. Imports can target Playlist, Queue or Saved / Favourites.
