Tasia Suno Connector
====================

1. In Chrome/Chromium open chrome://extensions
2. Enable Developer mode.
3. Click Load unpacked and select this tasia-suno-connector folder.
4. In Tasia Streamer: Settings -> Suno connection -> copy connector setup.
5. Open the extension popup and enter the Tasia Streamer URL and connector key.
6. Click Save & Sync, then open/use suno.com while logged in.
7. Tasia Streamer will show Suno connected when a current Bearer session is captured.

Security:
- The extension captures only Authorization Bearer tokens on Suno API requests.
- The token is stored in Chrome local extension storage and sent only to the Tasia Streamer URL you configure.
- The connector key maps the browser session to one Tasia Streamer user.
- Use HTTPS if the Streamer is not strictly local/private.
