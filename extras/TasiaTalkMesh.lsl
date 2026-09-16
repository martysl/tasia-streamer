// ============================================================
// Tasia Talk Mesh
// Talks to Tasia Streamer -> Tasia AI -> Edge TTS
//
// Setup:
// 1. Put your Tasia Streamer public URL in API_BASE (no trailing slash).
// 2. Copy the Mesh API key from Settings -> Tasia Talk into API_KEY.
// 3. Put this script in Tasia's mesh body/object.
// 4. Pick a face that can use Shared Media for MEDIA_FACE.
//
// Chat example:
//   @tasia hello there
//
// The script posts the request asynchronously, polls for completion,
// says the returned text in local chat, and loads the generated TTS
// player page on the configured media face.
//
// The API key is sent in JSON request bodies, not in URL query strings,
// so it does not get copied into ordinary HTTP access-log URLs.
// ============================================================

string API_BASE = "https://YOUR-TASIA-STREAMER";
string API_KEY  = "PASTE-MESH-API-KEY-HERE";

integer LISTEN_CHANNEL = 0;
string TRIGGER = "@tasia";
integer OWNER_ONLY = TRUE;

// Shared-media face used only as the audio player.
integer MEDIA_LINK = LINK_THIS;
integer MEDIA_FACE = 0;

float POLL_SECONDS = 2.0;
integer MAX_POLLS = 45;

integer gListen;
key gStartRequest = NULL_KEY;
key gPollRequest = NULL_KEY;
string gJob = "";
integer gPollCount = 0;

string endpoint(string path)
{
    return API_BASE + path;
}

integer ready()
{
    if (llSubStringIndex(API_BASE, "YOUR-TASIA-STREAMER") != -1)
        return FALSE;
    if (llSubStringIndex(API_KEY, "PASTE-MESH-API-KEY") != -1)
        return FALSE;
    return TRUE;
}

sayError(string message)
{
    llOwnerSay("Tasia Talk: " + message);
}

stopPolling()
{
    gJob = "";
    gPollCount = 0;
    gPollRequest = NULL_KEY;
    llSetTimerEvent(0.0);
}

playRemoteVoice(string mediaUrl)
{
    if (mediaUrl == "") return;

    llSetLinkMedia(
        MEDIA_LINK,
        MEDIA_FACE,
        [
            PRIM_MEDIA_CURRENT_URL, mediaUrl,
            PRIM_MEDIA_HOME_URL, mediaUrl,
            PRIM_MEDIA_AUTO_PLAY, TRUE,
            PRIM_MEDIA_FIRST_CLICK_INTERACT, FALSE,
            PRIM_MEDIA_PERMS_INTERACT, PRIM_MEDIA_PERM_OWNER,
            PRIM_MEDIA_PERMS_CONTROL, PRIM_MEDIA_PERM_OWNER,
            PRIM_MEDIA_WIDTH_PIXELS, 128,
            PRIM_MEDIA_HEIGHT_PIXELS, 64
        ]
    );
}

startTalk(string prompt)
{
    if (!ready())
    {
        sayError("configure API_BASE and API_KEY first.");
        return;
    }

    if (gStartRequest != NULL_KEY || gJob != "")
    {
        sayError("I am already thinking.");
        return;
    }

    prompt = llStringTrim(prompt, STRING_TRIM);
    if (prompt == "") return;

    string body = llList2Json(
        JSON_OBJECT,
        [
            "api_key", API_KEY,
            "prompt", prompt
        ]
    );

    gStartRequest = llHTTPRequest(
        endpoint("/api/tasia-talk/mesh"),
        [
            HTTP_METHOD, "POST",
            HTTP_MIMETYPE, "application/json"
        ],
        body
    );
}

pollJob()
{
    if (gJob == "" || gPollRequest != NULL_KEY) return;

    ++gPollCount;
    if (gPollCount > MAX_POLLS)
    {
        sayError("request timed out.");
        stopPolling();
        return;
    }

    string body = llList2Json(
        JSON_OBJECT,
        [
            "api_key", API_KEY
        ]
    );

    gPollRequest = llHTTPRequest(
        endpoint("/api/tasia-talk/mesh/status/") + gJob,
        [
            HTTP_METHOD, "POST",
            HTTP_MIMETYPE, "application/json"
        ],
        body
    );
}

default
{
    state_entry()
    {
        if (gListen) llListenRemove(gListen);
        gListen = llListen(LISTEN_CHANNEL, "", NULL_KEY, "");

        if (!ready())
            sayError("script loaded. Set API_BASE and API_KEY at the top of the script.");
    }

    on_rez(integer start)
    {
        llResetScript();
    }

    changed(integer change)
    {
        if (change & CHANGED_OWNER)
            llResetScript();
    }

    listen(integer channel, string name, key id, string message)
    {
        if (OWNER_ONLY && id != llGetOwner()) return;

        string lower = llToLower(message);
        string triggerLower = llToLower(TRIGGER);
        if (llSubStringIndex(lower, triggerLower) != 0) return;

        integer triggerLen = llStringLength(TRIGGER);
        string prompt = llStringTrim(
            llDeleteSubString(message, 0, triggerLen - 1),
            STRING_TRIM
        );

        startTalk(prompt);
    }

    timer()
    {
        pollJob();
    }

    http_response(key requestId, integer status, list metadata, string body)
    {
        if (requestId == gStartRequest)
        {
            gStartRequest = NULL_KEY;

            if (status < 200 || status >= 300)
            {
                sayError("start request HTTP " + (string)status + ": " + body);
                return;
            }

            string ok = llJsonGetValue(body, ["ok"]);
            string requestIdText = llJsonGetValue(body, ["request_id"]);
            if (ok != JSON_TRUE || requestIdText == JSON_INVALID || requestIdText == "")
            {
                sayError("bad start response: " + body);
                return;
            }

            gJob = requestIdText;
            gPollCount = 0;
            llSetTimerEvent(POLL_SECONDS);
            pollJob();
            return;
        }

        if (requestId == gPollRequest)
        {
            gPollRequest = NULL_KEY;

            if (status < 200 || status >= 300)
            {
                sayError("poll HTTP " + (string)status + ": " + body);
                stopPolling();
                return;
            }

            string jobStatus = llJsonGetValue(body, ["status"]);

            if (jobStatus == "done")
            {
                string text = llJsonGetValue(body, ["text"]);
                string mediaUrl = llJsonGetValue(body, ["media_url"]);

                if (text != JSON_INVALID && text != "")
                    llSay(0, text);

                if (mediaUrl != JSON_INVALID && mediaUrl != "")
                    playRemoteVoice(mediaUrl);

                stopPolling();
                return;
            }

            if (jobStatus == "error")
            {
                string error = llJsonGetValue(body, ["error"]);
                if (error == JSON_INVALID || error == "") error = "unknown server error";
                sayError(error);
                stopPolling();
                return;
            }

            // queued / working -> wait for the next timer tick.
            return;
        }
    }
}
