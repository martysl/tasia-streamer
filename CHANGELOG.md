## 2.0.0-beta29

- Added **All Sources** search across Spotify, Universal Search, Audius, and configured SoundCloud/Jamendo/Stremio catalogs.
- Spotify provider now accepts song/artist text searches as well as Spotify track URLs; selected Spotify tracks still resolve through BTCH for queue caching.
- All Sources keeps equivalent matches from different providers visible so the DJ can choose which service to use.
- TXT import now accepts either a `.txt` file or a pasted one-song-per-line list.
- TXT import adds **Find songs in**: Auto/All, Spotify, Universal, SoundCloud, Audius, Jamendo, Stremio, plus URL-only SoundCloud/Google Drive choices.
- Mixed lists auto-route Spotify, SoundCloud, Google Drive, YouTube and Suno URLs to the correct resolver regardless of the text-search selection.
- Auto import reuses an exact local-library match first, then searches all available online sources.

## 2.0.0-beta28

- Added `btch-downloader@6.3.6` to the Docker image.
- Added Spotify (BTCH), SoundCloud (BTCH), and Google Drive (BTCH) URL providers in Online / Universal.
- BTCH results support Queue, Playlist, and Saved using the existing catalog flow.
- Queueing a BTCH result resolves the media URL, downloads it into the private per-user cache, validates/transcodes it with FFmpeg, and indexes the cached MP3 in the user's library.
- Queue Playlist and per-item Playlist → Queue now correctly materialize every catalog provider reference, including BTCH entries.

## 2.0.0-beta27

- Rebuilt Suno authentication around a refreshable Clerk browser session, following the current public `paperfoot/suno-cli` protocol shape.
- Tasia Suno Connector v1.1 sends only the user's own Suno `__client` session value plus device ID after explicit pairing; it no longer captures Bearer request headers.
- Docker exchanges `__client` for a Clerk session ID + JWT and refreshes stale JWTs automatically.
- Suno clip resolution now prefers the verified `/api/feed/?ids=<uuid>` flow and uses the API-returned `audio_url`; no guessed `cdn1.suno.ai/<uuid>.mp3` URLs.
- Added automatic one-time API retry after a 401/auth-stale response.
- Added Settings → Suno `Refresh now` and a manual `__client` fallback for headless setups.
- Kept old Bearer sessions and signed-cookie support only as migration/legacy fallbacks.

## 2.0.0-beta26

- Suno: ignore `studio-api.../api/forbidden` placeholder URLs.
- Prefer API-provided `media_urls` over legacy `audio_url`, including progressive CloudFront M4A/Opus.
- Try both current Suno API host spellings and the newer `clips/get_songs_by_ids` endpoint before legacy feed-by-id.
- Retry all returned Suno audio candidates; a 403/MissingKey on one media URL now falls through to the next candidate.
- Convert successful M4A/Opus/AAC candidates to the normal private MP3 cache with ffmpeg.

- Fixed Suno connector key field appearing blank when the browser reused an older cached `app.js`.
- Added versioned static asset URLs and no-cache UI headers so upgrades always load matching HTML/JS/CSS.
- Connector generation now verifies the key through the status endpoint and writes it explicitly into the field.


- Fixed Suno Connector key generation: keys are now stored in SQLite user state instead of depending on `suno-session.json`.
- Existing beta23 connector keys migrate automatically when present.
- Added an explicit **Generate connector key** button and `/api/suno/connector/generate` endpoint.
- Copy connector setup auto-generates a key if the field is empty.
- Disconnecting Suno no longer removes or regenerates the connector pairing key.

## 2.0.0-beta23

- Reworked Suno playback around the authenticated session used by the Suno web app: Bearer token + device-id → `/api/feed/?ids=<uuid>` → fresh `audio_url` → private cache.
- Stopped constructing `https://cdn1.suno.ai/<uuid>.mp3` as the normal Suno playback path.
- Added the bundled **Tasia Suno Connector** Chrome/Chromium extension. It captures the user's changing Suno Bearer token from Suno API requests and forwards it only to the configured Tasia Streamer account.
- Added per-user connector keys, manual Bearer-token fallback, session disconnect and connector-key rotation.
- Kept beta22 signed-cookie support only as a legacy fallback for unusual CloudFront delivery.
- Suno favourites/library references now use stable `suno:<uuid>` identifiers rather than expiring signed media URLs.
- Added a downloadable connector ZIP at `/api/suno/connector/download`.

## 2.0.0-beta22

- Added per-user Suno authentication cookies for the new signed-CDN behavior (`Missing Key-Pair-Id`).
- Settings → Suno authentication accepts either a pasted browser `Cookie:` header or a Netscape `cookies.txt` export; secrets stay server-side under the user data directory with mode 0600.
- Suno song/share resolution now preserves fresh signed CDN query parameters when the Suno page exposes them instead of stripping them back to `cdn1.suno.ai/<uuid>.mp3`.
- If Suno uses signed CloudFront cookies, those cookies are attached to the CDN request; if it uses a signed URL, Tasia uses the signed URL returned by the page.
- Suno cache keys use the stable clip UUID so renewed/expiring signatures do not create duplicate cached tracks.
- Added a clear error explaining how to install Suno auth when CloudFront returns `Missing Key-Pair-Id`.

## 2.0.0-beta21

- Fixed Disconnect so it is a real hard disconnect: pause playout, request `shoutcast.stop`, then terminate the per-user Liquidsoap engine to guarantee the SHOUTcast source socket closes.
- Disconnect now clears stale Now Playing/progress state and unlocks any prefetched ON DECK row.
- Connect starts a fresh per-user engine; Pause remains the non-destructive way to stay connected while sending silence.
- Control buttons refresh status immediately after the command completes.

## 2.0.0-beta20

- Fixed editable Queue/Playlist position fields losing focus before you could type.
- The 2-second status refresh now keeps data fresh without rebuilding the list whose position input is focused.
- Enter submits the typed destination directly; Escape restores the current position.
- Drag/drop and editable numbers continue to coexist, with ON DECK rows protected.

## 2.0.0-beta18

- Fixed `/api/status`, queue and playlist timing crashing on imported `catalog:universal:...` references.
- Preview detection now stats only absolute local filesystem paths; provider/addon references are never passed to `pathlib`.
- Hardened local-library file checks against non-filesystem references and `OSError`/`ENAMETOOLONG`.

## 2.0.0-beta17

- Fixed startup crash after TXT/Universal imports: provider references such as `catalog:universal:...` are no longer treated as filesystem paths.
- Music path repair now touches only actual legacy paths under `/music`.
- Hardened local-library cleanup so malformed/provider references can never crash startup via `Path.exists()` / `stat()`.

## 2.0.0-beta16

- Fixed queue/playlist list breakage after TXT imports.
- Sanitizes imported/provider duration and metadata before JSON rendering so NaN/invalid values cannot break the whole list endpoint.
- Normalizes queue/playlist positions after import and on startup, repairing beta15 lists automatically.
- Compact provider badges in Queue/Playlist so batches of Universal Search imports no longer crush the row layout.
- Hardened imported rows so long titles/source metadata cannot push action buttons out of the pane.

## 2.0.0-beta15

- Added TXT set import to Playlist, Queue or Saved / Favourites.
- TXT supports plain song names, YouTube/Spotify links through Universal Search, Suno links/UUIDs, direct audio URLs, comments starting with `#`, and blank lines.
- Exact local-library matches are reused before online search.
- Added duplicate skipping, continue-on-error mode, and an import report with unresolved lines.
- Universal Search entries added to Playlist are now stored as provider references and converted only when moved to Queue, making large TXT set imports much faster.
- Queue Playlist now resolves/caches Universal entries before adding them to the live queue and reports failures.

## 2.0.0-beta14

- Added per-song ★ Save buttons to Queue and Playlist rows.
- Added browser-only ▶ preview buttons to Queue and Playlist whenever a cached/local audio file exists.
- Moved the local preview player into a global bottom dock so it remains visible from every workstation pane.
- Saving cached Universal, Suno, Jellyfin, WebDAV and FTP tracks from Queue/Playlist reuses the private cached library copy when possible.
- Saving uncached catalog rows preserves the provider track id for later resolution.
- Queue/Playlist preview endpoints are account-isolated and restricted to each user's private music/data roots.

## 2.0.0-beta13

- Added a browser-only **▶ Test** button for tracks in Local folders.
- Added a compact local preview player with play/pause/seek/stop controls.
- Local preview never touches Queue, Playlist, Liquidsoap, SHOUTcast, or broadcast transport state.
- Preview endpoint is authenticated and restricted to the current user's private music root.

# Changelog

## 2.0.0-beta12

- Fixed queue and playlist drag/reorder being interrupted by the 2-second UI refresh.
- Added explicit drag handles with Pointer Events for mouse/touch and native HTML5 drag fallback.
- Added `dataTransfer.setData()` for Firefox/native drag compatibility.
- Reorder is persisted on pointer release/drop and the list refreshes from the server afterward.

## 2.0.0-beta10
- Universal Search now uses yt-dlp only for search/result discovery; yt-dlp no longer downloads audio for Queue/Playlist.
- Selected Universal tracks are sent to a configurable per-user MP3 converter API, defaulting to `https://yapi.is-on.click/api/convert`.
- Converter POST JSON matches the supplied contract: `{url, format: "mp3", quality: "best", return_file: true}`.
- Supports direct audio/file responses and JSON responses containing `file_url`, `download_url`, `audio_url`, or `url`.
- Converter downloads are streamed to the user's private cache with the existing remote-size limit, then normalized and validated with FFmpeg before playout.
- Added MP3 converter API URL to Settings → Universal Search.

## 2.0.0-beta9
- Added built-in Bhariya-style Universal Search using current yt-dlp for song/artist text, YouTube URLs and Spotify URLs.
- Spotify links use Spotify oEmbed only for basic link metadata, then search for a matching YouTube result; no Spotify Web API key is required.
- Added optional per-user YouTube search `cookies.txt` upload/removal; the file stays server-side.
- Docker image installs yt-dlp with its EJS component plus Deno for YouTube search compatibility.
- SoundCloud remains available as a legacy optional catalog but is no longer the default online source.

## 2.0.0-beta8
- Merged the previously separate online-catalog branch back into the current Jellyfin + built-in-landing build.
- Restored **SoundCloud, Audius, Jamendo and Stremio Addon** as a third **Online catalogs** song-chooser tab.
- Restored per-user catalog settings with server-side secrets and Save/Test controls.
- Catalog tracks can be added to Queue/Playlist and resolve a fresh playable provider URL only when Liquidsoap asks for the next track.
- Kept **Jellyfin username/password** browsing, local/WebDAV/FTP sources, Suno, multi-user isolation, landing page and DJ AI in the same package.
- Restored SoundCloud playback through the current `/tracks/{track_urn}/streams` endpoint, preferring the modern AAC/HLS 160 kbps URL with 96 kbps fallback.
- Queue/Playlist/Now Playing now show provider badges and source links for online catalog tracks.
- Stremio integration accepts only direct HTTP(S) `stream.url` responses; torrent/infoHash, magnet, YouTube-ID, external-player and DRM-style transports remain unsupported.

## 2.0.0-beta7
- Added Jellyfin as a first-class remote music source using normal Jellyfin **username/password** authentication (no API key required).
- Jellyfin access tokens are obtained internally after login and kept out of browser responses; cached sessions are invalidated automatically if credentials change or Jellyfin returns HTTP 401.
- Added Jellyfin music-library, artist/album/folder and track browsing through the existing left-side source browser.
- Added remote-source search; Jellyfin search is server-side and WebDAV/FTP search filters the current folder.
- Jellyfin user library permissions are naturally respected because Tasia browses as the configured Jellyfin user.
- Selected Jellyfin tracks are downloaded to the current Tasia account's private cache, validated with ffprobe, then enter the same stable Queue/Playlist pipeline as every other source.
- Jellyfin internal item IDs are hidden from normal breadcrumb/folder labels in the workstation.

## 2.0.0-beta6
- Built the public Tasia Streamer landing page directly into the FastAPI application; no second website/container is required.
- `/` now serves the landing page, `/login` serves login/first-account setup, and `/app` serves the DJ workstation.
- Landing-page CTAs detect setup/session state and switch between first-time setup, Sign in, and Open workstation automatically.
- Added Home navigation from the workstation/login screen and normalized successful login/account-switch URLs to `/app`.
- Landing assets are bundled locally under `/static`; no external font/CDN dependency is required for the page itself.

## 2.0.0-beta5
- Replaced the local-library folder dropdown with a real file-browser view that shows physical folders, subfolders and songs together.
- Added folder-level **+Queue** and **+Playlist** actions; they recursively include the folder's complete song tree for quick party sets.
- Added global per-user library search across title, artist and path, with live result counts/duration.
- Added breadcrumb/root/up navigation for the private local library.
- Added optional per-user DJ AI settings: OpenAI-compatible Base URL, API key, model and custom system prompt.
- Added a DJ AI adviser dialog with quick prompts and current On Air/Queue/Playlist/library context. AI is advice-only and never changes playback automatically.
- AI API keys stay server-side and are omitted from settings responses.
- Optimized recursive folder track/duration statistics into a single library pass so large music collections do not get rescanned once per visible folder.

## 2.0.0-beta4
- Fixed startup crash `UNIQUE constraint failed: user_library.user_id, user_library.path` during beta2/beta3 private-folder repair.
- Path repair now detects when a stale library row and an already-correct row resolve to the same physical file, merges useful metadata, and removes only the duplicate row.
- Applied the same collision-safe logic to both one-time legacy path rewriting and normal startup path repair so the error cannot recur through either migration path.

## 2.0.0-beta3
- Fixed beta2 local playback regression after private-folder migration by repairing stale `/music/...` queue/library/playlist paths against the owning user folder.
- Startup now repairs ownership/read-write access inside `/music/users/...` while leaving unrelated top-level host music ownership alone.
- Connect/Play run a lightweight private-path repair before starting playout.
- Added visible playback-path errors instead of silently skipping unreadable files.
- WebDAV now retries HTTP Digest authentication automatically when a server rejects Basic with a `401` Digest challenge.
- WebDAV URLs may contain credentials, while explicit username/password fields still take priority.
- FTP/FTPS login and browse failures now return useful `530`/connection messages; older FTP servers without `MLSD` fall back to `NLST`.
- Added **Test connection** for WebDAV/FTP and Save validates the source before storing it.
- Fixed the beta2 create-user session-switch race that could leave subsequent API calls returning HTTP 401.

## 2.0.0-beta2
- Private local music roots per account: `/music/users/<id>-<username>/` and all subfolders.
- Existing shared `/music` content is migrated once to the first account so other users cannot see it.
- Uploads now land inside each account's private `Uploads/` folder.
- Fixed admin user creation dialog and added optional automatic switch/open of the newly created account.
- Create-user buttons no longer accidentally submit/close the dialog form.

## 2.0.0-beta1
- Multi-user username/password sessions and admin-created accounts.
- Per-user queue, playlist, library index, state, remote sources and SHOUTcast settings.
- Per-user Liquidsoap engines with independent control and metadata ports.
- First-user migration/adoption from v1.x library/queue/playlist tables.
- Suno full URL, short share URL, bare UUID and CDN resolution.
- Queue and Playlist drag/drop ordering.
- Queue ETA, set end clock, playlist offsets and total set lengths.
- SAM-style three-column workstation layout.
- Folder-aware local library.
- WebDAV and FTP/FTPS folder browser with local caching before playout.
- Host-mounted `/data` ownership bootstrap before dropping privileges to `liquidsoap`.

## 1.3.2
- Last v1.x line.

## 2.0.0-beta12

- Added per-user Saved / Favourites library with search.
- Save Universal/catalog results once and reuse them without searching again.
- Added Suno / direct-audio favourites without downloading until Queue/Playlist.
- Added favourites for Jellyfin/WebDAV/FTP remote tracks and local-library tracks.
- Saved online/remote items resolve and cache only when queued or added to the set.
