# Tasia Talk

Tasia Talk adds optional AI voice links between songs and exposes the same Tasia AI + Edge TTS voice to a Second Life/OpenSim mesh body.

## Voice

The default voice is the Tasia voice already used by the project:

```text
Voice: en-US-AnaNeural
Rate:  +20%
Pitch: +50Hz
Volume: +0%
```

The values can be changed per Tasia Streamer account in **Settings -> Tasia Talk**.

## Between-song radio talk

Enable **Speak between songs** in Settings. Tasia Streamer then:

1. Selects a normal music track with the existing scheduler.
2. Starts preparing a short DJ comment in the background using the account's existing **DJ AI** Base URL, API key and model.
3. Generates the spoken line with Edge TTS.
4. Inserts the generated MP3 as a real scheduler item after the song.
5. Continues with the next song normally.

The generated speech is stored under the current user's private `/data/users/<id>/tts/` directory and old files are cleaned automatically.

The scheduler allows a longer prefetch window for Tasia AI. If AI/TTS generation still takes too long, that one comment is skipped rather than stopping the music.

## DJ AI requirement

Tasia Talk reuses the existing **DJ AI** OpenAI-compatible connection. Configure:

```text
Base URL
Model
API key (when required)
```

The normal DJ adviser and Tasia Talk share the connection, but Tasia Talk uses its own spoken-radio/mesh prompt. An optional Tasia Talk persona prompt can override the built-in spoken persona.

## Mesh / LSL API

Each Tasia Streamer account receives its own rotatable Mesh API key. It is shown only to the logged-in account in **Settings -> Tasia Talk**.

Start a request:

```http
POST /api/tasia-talk/mesh
Content-Type: application/json

{
  "api_key": "YOUR_MESH_KEY",
  "prompt": "Hello Tasia"
}
```

Immediate response:

```json
{
  "ok": true,
  "request_id": "...",
  "status": "queued"
}
```

Poll without keeping one long HTTP request open. The preferred polling route is also POST so the API key stays in the JSON body instead of appearing in access-log URLs:

```http
POST /api/tasia-talk/mesh/status/<request_id>
Content-Type: application/json

{
  "api_key": "YOUR_MESH_KEY"
}
```

While processing:

```json
{
  "ok": true,
  "status": "working"
}
```

When ready:

```json
{
  "ok": true,
  "status": "done",
  "text": "Tasia's reply",
  "duration": 4.2,
  "audio_url": "https://streamer.example/api/tasia-talk/audio/...",
  "media_url": "https://streamer.example/api/tasia-talk/media/..."
}
```

Generated public audio/media tokens expire automatically.

## LSL

The ready-to-edit script is:

```text
extras/TasiaTalkMesh.lsl
```

It can also be downloaded from the Tasia Talk block in Settings.

At the top of the script set:

```lsl
string API_BASE = "https://YOUR-TASIA-STREAMER";
string API_KEY  = "YOUR-MESH-API-KEY";
```

Then choose a face on the Tasia mesh object for Shared Media:

```lsl
integer MEDIA_LINK = LINK_THIS;
integer MEDIA_FACE = 0;
```

The default chat trigger is:

```text
@tasia hello
```

The script sends the prompt asynchronously, polls with the API key in the POST body until the AI/TTS job finishes, says the returned text in local chat, then loads the generated TTS player on the configured Shared Media face.

### Shared Media note

Second Life/OpenSim LSL cannot pass an arbitrary remote MP3 URL to `llPlaySound`; that function expects an in-world sound asset. Therefore the supplied script uses Shared Media on one mesh face for the generated remote TTS audio. Viewer media/autoplay permissions must allow that face to play media.

## Browser/API routes

```text
GET  /api/settings/tasia-talk
PUT  /api/settings/tasia-talk
POST /api/tasia-talk/test
POST /api/tasia-talk/key/rotate
POST /api/tasia-talk/mesh
POST /api/tasia-talk/mesh/status/<request_id>
GET  /api/tasia-talk/audio/<token>
GET  /api/tasia-talk/media/<token>
GET  /api/tasia-talk/lsl
```

The settings/test/key/LSL download routes require a normal logged-in Tasia Streamer session. The external mesh routes use the per-user Mesh API key.
