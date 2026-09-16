// ============================================================
// Tasia Talk Mesh -> Tasia Streamer radio
//
// What happens:
//   SL/OpenSim chat or a nearby visitor
//        -> this LSL script
//        -> Tasia Streamer AI
//        -> Edge TTS voice is queued ON THE RADIO STREAM
//        -> the exact same AI text is returned here and spoken in local chat
//
// Setup:
// 1. Put your public Tasia Streamer URL in API_BASE (no trailing slash).
// 2. Copy Settings -> Tasia Talk -> Mesh API key into API_KEY.
// 3. Put the script into Tasia's mesh/body object.
//
// Chat:
//   @tasia hello!
//
// Visitors are greeted automatically once per GREET_COOLDOWN_SECONDS.
// The API key is always sent in JSON bodies, not URL query strings.
// ============================================================

string API_BASE = "https://YOUR-TASIA-STREAMER";
string API_KEY  = "PASTE-MESH-API-KEY-HERE";

integer LISTEN_CHANNEL = 0;
string TRIGGER = "@tasia";
integer ALLOW_ANYONE_CHAT = TRUE;

integer AUTO_GREET = TRUE;
float SENSOR_RANGE = 18.0;
float SENSOR_INTERVAL = 8.0;
integer GREET_COOLDOWN_SECONDS = 3600;
integer MAX_REMEMBERED_VISITORS = 40;

float POLL_SECONDS = 2.0;
integer MAX_POLLS = 50;
integer MAX_PENDING_PROMPTS = 6;

integer gListen;
key gStartRequest = NULL_KEY;
key gPollRequest = NULL_KEY;
string gJob = "";
integer gPollCount = 0;
list gPendingPrompts = [];

// Pairs: avatar key, last greeting Unix time.
list gVisitorHistory = [];

string endpoint(string path)
{
    return API_BASE + path;
}

integer ready()
{
    if (llSubStringIndex(API_BASE, "YOUR-TASIA-STREAMER") != -1) return FALSE;
    if (llSubStringIndex(API_KEY, "PASTE-MESH-API-KEY") != -1) return FALSE;
    return TRUE;
}

integer busy()
{
    return (gStartRequest != NULL_KEY || gJob != "");
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

rememberVisitor(key id)
{
    integer now = llGetUnixTime();
    integer i;
    integer n = llGetListLength(gVisitorHistory);

    for (i = 0; i < n; i += 2)
    {
        if ((key)llList2String(gVisitorHistory, i) == id)
        {
            gVisitorHistory = llListReplaceList(gVisitorHistory, [id, now], i, i + 1);
            return;
        }
    }

    gVisitorHistory += [id, now];

    while (llGetListLength(gVisitorHistory) > MAX_REMEMBERED_VISITORS * 2)
        gVisitorHistory = llDeleteSubList(gVisitorHistory, 0, 1);
}

integer greetedRecently(key id)
{
    integer now = llGetUnixTime();
    integer i;
    integer n = llGetListLength(gVisitorHistory);

    for (i = 0; i < n; i += 2)
    {
        if ((key)llList2String(gVisitorHistory, i) == id)
        {
            integer last = llList2Integer(gVisitorHistory, i + 1);
            if ((now - last) < GREET_COOLDOWN_SECONDS) return TRUE;
            return FALSE;
        }
    }
    return FALSE;
}

startTalk(string prompt)
{
    if (!ready())
    {
        sayError("configure API_BASE and API_KEY first.");
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
        [HTTP_METHOD, "POST", HTTP_MIMETYPE, "application/json"],
        body
    );
}

enqueuePrompt(string prompt)
{
    prompt = llStringTrim(prompt, STRING_TRIM);
    if (prompt == "") return;

    if (!busy())
    {
        startTalk(prompt);
        return;
    }

    if (llGetListLength(gPendingPrompts) < MAX_PENDING_PROMPTS)
        gPendingPrompts += [prompt];
}

startNextPrompt()
{
    if (busy()) return;
    if (llGetListLength(gPendingPrompts) == 0) return;

    string prompt = llList2String(gPendingPrompts, 0);
    gPendingPrompts = llDeleteSubList(gPendingPrompts, 0, 0);
    startTalk(prompt);
}

pollJob()
{
    if (gJob == "" || gPollRequest != NULL_KEY) return;

    ++gPollCount;
    if (gPollCount > MAX_POLLS)
    {
        sayError("AI/TTS request timed out.");
        stopPolling();
        startNextPrompt();
        return;
    }

    string body = llList2Json(JSON_OBJECT, ["api_key", API_KEY]);

    gPollRequest = llHTTPRequest(
        endpoint("/api/tasia-talk/mesh/status/") + gJob,
        [HTTP_METHOD, "POST", HTTP_MIMETYPE, "application/json"],
        body
    );
}

default
{
    state_entry()
    {
        if (gListen) llListenRemove(gListen);
        gListen = llListen(LISTEN_CHANNEL, "", NULL_KEY, "");

        if (AUTO_GREET)
            llSensorRepeat("", NULL_KEY, AGENT, SENSOR_RANGE, PI, SENSOR_INTERVAL);

        if (!ready())
            sayError("script loaded. Set API_BASE and API_KEY at the top of the script.");
    }

    on_rez(integer start)
    {
        llResetScript();
    }

    changed(integer change)
    {
        if (change & CHANGED_OWNER) llResetScript();
    }

    listen(integer channel, string name, key id, string message)
    {
        if (!ALLOW_ANYONE_CHAT && id != llGetOwner()) return;

        string lower = llToLower(message);
        string triggerLower = llToLower(TRIGGER);
        if (llSubStringIndex(lower, triggerLower) != 0) return;

        integer triggerLen = llStringLength(TRIGGER);
        string words = llStringTrim(
            llDeleteSubString(message, 0, triggerLen - 1),
            STRING_TRIM
        );
        if (words == "") return;

        // Include the speaker name so AI can answer them naturally.
        enqueuePrompt(name + " said to you in local chat: " + words);
    }

    sensor(integer count)
    {
        if (!AUTO_GREET || !ready()) return;

        list names = [];
        integer i;
        for (i = 0; i < count; ++i)
        {
            key id = llDetectedKey(i);
            if (id == NULL_KEY) jump next_avatar;

            // Don't repeatedly greet the same avatar every sensor pass.
            if (!greetedRecently(id))
            {
                string visitor = llDetectedName(i);
                if (visitor != "") names += [visitor];
                rememberVisitor(id);
            }
@next_avatar;
        }

        integer newCount = llGetListLength(names);
        if (newCount > 0)
        {
            string joined = llDumpList2String(names, ", ");
            enqueuePrompt(
                "These people just arrived near you: " + joined
                + ". Greet them warmly by name, welcome them to the party and radio stream, and keep it short."
            );
        }
    }

    no_sensor()
    {
        // Nothing to do. Visitor history remains so returning avatars are not spammed.
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
                startNextPrompt();
                return;
            }

            string ok = llJsonGetValue(body, ["ok"]);
            string requestIdText = llJsonGetValue(body, ["request_id"]);
            if (ok != JSON_TRUE || requestIdText == JSON_INVALID || requestIdText == "")
            {
                sayError("bad start response: " + body);
                startNextPrompt();
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
                startNextPrompt();
                return;
            }

            string jobStatus = llJsonGetValue(body, ["status"]);

            if (jobStatus == "done")
            {
                string text = llJsonGetValue(body, ["text"]);
                string streamQueued = llJsonGetValue(body, ["stream_queued"]);

                // EXACT same AI line as the generated Edge-TTS radio voice.
                if (text != JSON_INVALID && text != "") llSay(0, text);

                if (streamQueued != JSON_TRUE)
                    sayError("reply text arrived, but radio voice was not queued.");

                stopPolling();
                startNextPrompt();
                return;
            }

            if (jobStatus == "error")
            {
                string error = llJsonGetValue(body, ["error"]);
                if (error == JSON_INVALID || error == "") error = "unknown server error";
                sayError(error);
                stopPolling();
                startNextPrompt();
                return;
            }

            // queued / working: wait for next timer tick.
            return;
        }
    }
}
