# Tasia Talk

Tasia Talk adds optional AI voice links between songs and connects a Second Life/OpenSim Tasia mesh body to the same AI + Edge TTS voice used by the radio.

## Voice

The default Tasia voice is:

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

If AI/TTS generation is too slow for the prefetch window, that one automatic comment is skipped rather than stopping the music.

## LSL -> radio bridge

The LSL bridge does **not** play remote audio on the mesh object. Instead:

```text
SL/OpenSim chat or visitor arrival
        -> LSL
        -> Tasia Streamer
        -> Tasia AI creates one reply
        -> Edge TTS generates the same reply
        -> voice is queued into the radio stream
        -> exact text is returned to LSL
        -> Tasia writes it in local chat
```

LSL-triggered speech is treated as live Tasia speech and takes priority over an automatically prepared between-song comment. It never interrupts the song currently on air; it is inserted at the next safe scheduler transition. Liquidsoap may already have one normal item ON DECK, so a live reply can occasionally follow that already-prefetched item.

## Automatic greetings

`extras/TasiaTalkMesh.lsl` includes a nearby-avatar sensor. By default it:

- scans within 18 metres every 8 seconds;
- remembers visitors for one hour;
- sends newly detected avatar names to Tasia AI;
- asks Tasia to greet them by name and welcome them to the party/radio;
- writes the resulting Tasia line in local chat;
- queues the matching Edge-TTS voice on the radio stream.

The sensor distance, interval and greeting cooldown are configurable at the top of the LSL script.

## Chat

The default local-chat trigger is:

```text
@tasia hello
```

The script includes the speaker's avatar name in the AI request so Tasia can answer them naturally. `ALLOW_ANYONE_CHAT` can be changed in the LSL configuration.

## DJ AI requirement

Tasia Talk reuses the existing **DJ AI** OpenAI-compatible connection. Configure:

```text
Base URL
Model
API key (when required)
```

The normal DJ adviser and Tasia Talk share the connection, but Tasia Talk uses its own spoken-radio/SL prompt. An optional Tasia Talk persona prompt can override the built-in spoken persona.

## LSL API

Each Tasia Streamer account receives its own rotatable LSL API key. It is shown only to the logged-in account in **Settings -> Tasia Talk**.

Start a request:

```http
POST /api/tasia-talk/mesh
Content-Type: application/json

{
  "api_key": "YOUR_LSL_KEY",
  "prompt": "Marty said to you in local chat: hello"
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

Poll without keeping one long HTTP request open. The API key stays in the JSON body instead of appearing in the URL:

```http
POST /api/tasia-talk/mesh/status/<request_id>
Content-Type: application/json

{
  "api_key": "YOUR_LSL_KEY"
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
  "text": "Hey Marty, welcome back!",
  "duration": 3.7,
  "stream_queued": true
}
```

The `text` field is exactly the spoken AI line used to generate the Edge-TTS radio audio.

## LSL setup

The ready-to-edit script is:

```text
extras/TasiaTalkMesh.lsl
```

It can also be downloaded from the Tasia Talk block in Settings.

Set:

```lsl
string API_BASE = "https://YOUR-TASIA-STREAMER";
string API_KEY  = "YOUR-LSL-API-KEY";
```

Useful options:

```lsl
integer ALLOW_ANYONE_CHAT = TRUE;
integer AUTO_GREET = TRUE;
float SENSOR_RANGE = 18.0;
float SENSOR_INTERVAL = 8.0;
integer GREET_COOLDOWN_SECONDS = 3600;
```

No Shared Media face is required for Tasia Talk. The generated voice is played by Tasia Streamer on the radio output, not by the SL/OpenSim object.

## Browser/API routes

```text
GET  /api/settings/tasia-talk
PUT  /api/settings/tasia-talk
POST /api/tasia-talk/test
POST /api/tasia-talk/key/rotate
POST /api/tasia-talk/mesh
POST /api/tasia-talk/mesh/status/<request_id>
GET  /api/tasia-talk/lsl
```

The settings/test/key/LSL download routes require a normal logged-in Tasia Streamer session. The external LSL routes use the per-user LSL API key.
