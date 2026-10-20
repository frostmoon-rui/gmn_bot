# Lucky Cat user guide

## Website interaction

Use `help website` for the in-chat guide. Mention the bot in groups; private-chat commands can be sent directly.

- `website screenshot:home` opens the public homepage at https://frostmoonrui.com and returns its current screenshot and URL.
- Pages: `home`, `about`, `friends`, `resources`, `food`, `projects`.
- Full syntax: `website screenshot:page language theme`. Example: `website screenshot:friends ja light`.
- Languages: `zh`, `en`, `ru`, `ja`; themes: `dark`, `light`. Public source defaults to `en dark`.
- `recommended links` and `friend application` are screenshot shortcuts, not form submission or publishing commands. Open the returned link to apply yourself; public listing requires approval.

Captures run only on request, with one capture at a time and a 30-second per-user cooldown. Results return only to the requesting chat and never enter the sticker library. The integration uses an isolated browser session, does not enter the owner studio or unlisted room, and cannot browse arbitrary URLs, fill forms, click controls or publish edits. The local food-map address `http://127.0.0.1:8788` is a separate host-only service.

Install optional browser support in the same Python environment as the bot; see [website screenshot setup](WEBSITE_SCREENSHOTS.md). Missing dependencies or unavailable pages produce a short failure, never a stale image presented as current.

Mention the bot in groups; enter commands directly in private chat. Use `cat help` to open concise help. Merely mentioning the word "manual" in conversation does not open it.

## Everyday commands

- `generate image:description`: generate a person, landscape, object, poster, or other requested image. Without a description, a default cat portrait is used.
- `summarize video:URL`, or reply to a video card and mention the bot with `summarize video`: fetch public information in the background and produce a short MiMo 2.6 Flash summary, normally 2–3 sentences and at most 180 characters, without links by default. You can also ask about your own most recent card in the last 30 minutes. Sharing alone does not trigger a reply.
- `set location:country city`: bind your own region. A province alone uses its capital as a reference while retaining your original record. Only a superadmin can bind another person's location.
- `weather today` / `weather tomorrow`: query your bound region without disclosing the place name in the report. Missing data is identified rather than invented.
- `remind me 10 minutes rest` / `remind me tomorrow 08:30 go out`: create a personal reminder in the original chat, using Beijing time. Use `my reminders` and `cancel reminder ID` to manage it.
- `call me CacheTide` / `use they/them for me` / `identity settings:gender identity|pronouns|preferred name`: explicitly record your preferences. Use `view my identity` and `clear my identity` to inspect or clear them.
- `comfort me` / `keep me company` / `grounding exercise` / `mental health support` / `first aid:situation`: respond without a model. These shortcuts do not add notebook records, forward requests for help, or contact other people. The chat platform may retain messages.
- `bot status`: show uptime, preferred model, sticker count, and group mode without probing APIs.
- `do not disturb on` / `do not disturb off`: pause or restore automatic weather and earthquake care. Direct queries and reminders you explicitly created remain available.
- `clear context`: clear this chat's short-term context without deleting notebook records. Group use requires a group admin or superadmin.

## Search

For ordinary questions, the current conversation model decides whether retrieval is needed and returns an internal search plan. Python validates and executes the plan; ordinary messages are not intercepted by a fixed list of keywords. Definitions, explanations, and other background questions can retrieve English or Chinese Wikipedia entries as needed, then use other pages if necessary. Time-sensitive questions prioritize recent official information; encyclopedia background alone cannot establish the latest situation. No complete Wikipedia download is required.

Explicit search commands are restricted to allowlisted users: `search:question` uses MiMo 2.6 Flash, while `ask assistant:question` uses Gemini planning and MiMo 2.6 Pro synthesis. `web search:question` and `search report:question` are also accepted. Automatic retrieval uses Flash. Replies normally contain 2–4 sentences, at most 240 characters, without long source links or report outlines. Use `view sources` in the same chat to see your recent sources from the last 30 minutes; mention the bot in groups. Tracking parameters are removed from links where supported, including Bilibili links.

Python retrieves material in a separate subprocess; models summarize text without AI browsing tools. Each person may have one search running, with a five-minute background limit. Automatic retrieval has a 30-second cooldown and supports up to four concurrent jobs; time-sensitive results are cached briefly. Rate limits, insufficient evidence, and failed summaries produce concise explanations rather than dumps of raw excerpts. Normal model token charges still apply.

## Maps and travel

Amap is the default provider. `amap map:full place` returns a map image. Other commands include:

~~~text
search places:city|keyword
search nearby:full place|keyword|radius in meters
route:origin -> destination
driving route:origin -> destination
walking route:origin -> destination
cycling route:origin -> destination
transit route:origin -> destination
measure distance:origin -> destination
~~~

Distance measurement uses driving distance. For places abroad, use `Google map:place`, `Google search places:keyword`, and `Google route:origin -> destination`. `air quality:city` and `local timezone:city` also use Google.

### Custom maps

~~~text
custom map:weekend trip|Beijing Palace Museum;Beijing Temple of Heaven;Beijing Summer Palace
my maps
view map:weekend trip
add map point:weekend trip|Beijing Beihai Park
remove map point:weekend trip|B
~~~

Full places are resolved automatically, duplicate coordinates are merged, and the saved map is returned as a marked image. Each map has 1–10 points due to static map API limits. Existing names are not overwritten. Maps are isolated by user and chat. Use `view map:weekend trip|connect` to connect markers in order; these lines are not actual roads. Use `delete map:weekend trip|confirm` to delete a whole map while retaining a local recovery record. The suffixes accept capitalization and surrounding spaces.

No locations are automatically collected from members' weather bindings or messages. Results explicitly requested in a group are visible in that group. See `help custom maps` and `help advanced maps`.

### Advanced map tools

~~~text
geocode:full address
reverse geocode:longitude,latitude
convert coordinates:gps|longitude,latitude
administrative area:city
input suggestions:city|keyword
place details:POI ID
polygon search:longitude,latitude;longitude,latitude;longitude,latitude|keyword
road traffic:city|road
nearby traffic:full place|radius in meters
rectangle traffic:bottom-left longitude,latitude|top-right longitude,latitude
transit stop:city|stop name
transit line:city|line name
transit stop ID:ID
transit line ID:ID
amap weather:city
~~~

Coordinate conversion also accepts `baidu` and `gcj02`. Use `IP location:public IPv4 address` only with an explicitly supplied address; it does not discover members' IPs. Nearby traffic is limited to a 5,000-meter radius; rectangle traffic is limited to a 10-kilometer diagonal. Transit information is not live vehicle arrival data. Coordinates default to GCJ-02, longitude first; do not mix in unconverted Google/GPS coordinates.

Amap service settings are in `amap_service.py`; `AMAP_WEB_KEY` overrides the configured web-service key. Requests run in the background with a shared per-process rate limit of approximately 0.95 requests/second, up to two map jobs, and query deadlines. Successful interface results are cached, up to 128 entries. Traffic caches last 60 seconds, current weather 10 minutes, and forecasts 30 minutes. Separate bot processes do not share rate limits.

Quota and permission failures are reported without buying quota or enabling paid services. Python downloads static map images and sends them to QQ as Base64, avoiding API URLs that expose keys. Private maps use `amap_maps.json`; deletion recovery records use `amap_maps_archive.json`, both created as needed. Custom maps are local collections, not cloud GeoHUB projects. Interactive JavaScript maps require a separate JS API key and security settings. Falcon tracking, geofences, and cloud datasets require their own permissions and business objects; they are not automatically created or used to track people.

References: [Amap static maps](https://lbs.amap.com/api/webservice/guide/api/staticmaps), [routes](https://lbs.amap.com/api/webservice/guide/api/direction), [weather](https://lbs.amap.com/api/webservice/guide/api-advanced/weatherinfo), [traffic](https://lbs.amap.com/api/webservice/guide/api-advanced/traffic-situation-inquiry), and [transit](https://lbs.amap.com/api/webservice/guide/api-advanced/bus-inquiry).

The optional manual `python tools/check_amap.py --live --preview` check uses existing quota and saves preview images; `--extended` checks additional read-only interfaces. It sends no QQ messages and does not change personal maps. Without `--live`, it makes no network requests.

## Food map

The bot starts a local map service at `http://127.0.0.1:8788`; this is a host-only address, not a public website. The initial import contains public facts for 26 venues in eight cities from [CacheTide's food map](https://cachetide.top/food-map). Original article URLs are retained; reviews, companions, photographs, and ratings are not copied.

Use `submit food:city|full venue name|full address|category|reason`, optionally followed by an original HTTPS source URL. Ordinary submissions require review. Superadmins use `pending food`, `approve food:ID`, and `remove food:ID`. See the [food map documentation](food_map/README.md).

## Video information

Video cards are read from NapCat's OneBot `message` array, including `json.data.data`, XML share fields, and `video.url/file`, with CQ escaping supported. An uncached quoted message can be retrieved with `get_msg`, after checking that it belongs to the same chat. A generic card-message log entry does not contain enough information to reconstruct its URL.

Bilibili retrieval attempts public metadata and up to 60 deduplicated comments, merging pinned/hot/recent entries and selected reply threads. Collection uses at most four comment requests with a 15-second scheduling budget; an in-flight request retains its normal timeout. Repeated, rate-limited or restricted responses stop collection and preserve the acquired sample. A reported comment total is not the number actually collected, and the sample is never presented as all audience opinion. Other sites use readable page metadata/VideoObject data. Login requirements, verification, rate limits, or unavailable comments are identified as missing information. Summaries infer from page content; they do not watch or listen to the video. A native QQ video URL may not have a public page with a title and comments. Jobs have a three-minute limit and return available information on failure, without AI browsing tools, login cookies, or automatically enabling paid APIs.

NapCat references: [message segments](https://doc.napneko.icu/onebot/segment), [APIs including get_msg](https://napneko.github.io/onebot/api), and [file/link refresh](https://napneko.github.io/develop/file).

## Permissions and quiet operation

New groups default to normal mode: light repetition and occasional interjections, without join/leave announcements. Repetition requires two different members to send identical short text consecutively within 90 seconds, then repeats only once with a 60-second cooldown. Random interjections use a separate message count per group (5–15 messages), are at least five minutes apart, and contain at most 30 characters. Repetition, random interjections, and join notices have independent cooldowns, consumed only after successful sending.

Group admins and superadmins can use `normal mode` to restore default interaction, `quiet mode` to pause repetition/interjections, and `active mode` to add limited join/leave interaction. Existing manually selected quiet modes remain in effect. Settings are independent by group and take effect without restarting; use `bot status` to inspect them.

Superadmin commands include `switch model to Gemini` (also `GPT`, `Claude`, or `DeepSeek`), `allowlist add QQ`, `allowlist remove QQ`, `blocklist add QQ`, `blocklist remove QQ`, `important allowlist add QQ`, `important allowlist remove QQ`, `view important allowlist`, `person record:QQ information`, `organize notebook`, and `organize portraits`. Full location profiles are shown only in a superadmin's private chat.

Permissions and forms of address are separate. Accounts explicitly configured in the local administrator registry have superadmin permissions. Preferred names are configured independently; matching a nickname grants no access. Only the original administrator can grant another account access with `superadmin add:QQ|preferred name`. Administrator identities are not published by the website.

Allowlisted users can use `view notebook`, `view full notebook`, `retract`, `add trigger word`, and `remove trigger word`. Explicit notebook entries use `notebook:content`; merely mentioning "notebook" does not create a record.

Important allowlist members receive a check of tomorrow's weather at 22:30 Beijing time. Delivery uses the group explicitly selected when adding the member, or private chat if added privately. Do-not-disturb pauses these notifications. Automatic earthquake care applies only to explicitly subscribed important members with a province-level association; `earthquake report` replies only in the current chat.

### Earthquake information

Mention the bot with `earthquake warning` or `latest earthquake` for an independent background check. Per the [Wolfx API documentation](https://wolfx.jp/zh/docs/open-api/), `cenc_eqlist.json` provides post-event bulletins and `cenc_eew.json` / `sc_eew.json` provide warning data. The parser accepts `Magnitude` and the legacy spelling, sorts by event time, rejects future timestamps, and excludes canceled warnings or those older than three minutes.

A separate 20-second polling loop avoids waiting for the half-hour weather cycle; failed sources back off for 60 seconds. Notification attempts remain restricted to explicitly subscribed important members, province-associated events of M >= 4.5, and persistent deduplication. There is no broadcast to every group. A province association does not establish shaking at a person's location. Third-party sources, networks, and QQ introduce delays; the bot provides no reliable countdown and cannot replace official warnings or local instructions.

## Maintenance and storage

Install with `python -m pip install -r requirements.txt`; run with `python gemini_bot.py`. Data paths are anchored to the application directory.

Offline checks: `python check_project.py` and `python -m unittest discover -s tests -q`. These do not start the bot, call APIs, or send QQ messages. The older `test_image_flow.py` is a separate manual message-sending probe.

Weather is fetched only for the region needed by a query, greeting or due configured notification. Startup and periodic maintenance do not sweep bound regions. Requests for the same region share a ten-minute cache and are serialized to avoid duplicate concurrent provider calls. Weather combines Amap, Google, verified Moji public pages, Open-Meteo, and configured MSN/Moji adapters in parallel. Only matching local dates are combined, each field retains its source, and conflicts are not averaged. Other providers can fill regions unsupported by Google. Open-Meteo can supplement missing apparent temperature, UV, wind speed, and hourly precipitation, explicitly labeled as model estimates. Modeled history is not station observation. See [weather providers and MSN integration](WEATHER_PROVIDERS.md). Satellite cloud images, point lightning, and typhoon tracks are not currently provided; unavailable data does not establish safety.

Reports include supported fields rather than repetitive missing-data statements. Where Google WGS84 coordinates are available, Open-Meteo can fill gaps without mixing Amap GCJ-02 coordinates. `weather_history.json` stores eight days of temperature snapshots by source; actual snapshot comparisons require yesterday's data at a similar time. A new installation omits that comparison when no history exists. Weather has separate context and concise response rules, does not insist people remain indoors, and does not infer UV or cold solely from rain. Weather history is never exposed to the public food map.

### Sticker library and image recognition

Ordinary pictures are not automatically described or collected. Only explicit sticker subtype 1 (or a named sticker/emoji subtype) and verified GIF file headers qualify; filenames, CDN paths, MIME claims and subtype 2 alone do not qualify. Non-GIF originals above 1 MiB are rejected even with a sticker flag. GIFs retain their original bytes. There is no application-level collection count cap, but disk capacity still applies. Images live in `stickers/images/` and the SQLite index in `stickers/index.sqlite3`. Sending encodes only the selected original file as Base64 rather than repeatedly loading and rewriting an entire image JSON file. Run `python tools/clean_stickers.py` to review oversized non-GIF originals, then add `--apply` to back up the SQLite index and matching originals under local `backup/` before removing them from the collection. The threshold refers to decoded image bytes, not Base64 text length. Back up or move the whole `stickers/` directory; use SQLite backup tools for a running database or stop the bot first.

Initial migration processes all current legacy records, without a 150-image cutoff. The original JSON remains in `backup/stickers_legacy_*.json`, while `stolen_emojis.json` becomes a small migration manifest. Broken links/records remain in backups and the pending index rather than being silently discarded. Migration cannot recover images already deleted by a historical 150-image cap.

Identical files, identical pixels in different encodings, and very similar compressed/resized copies are merged, preferring a clearer version while trying to preserve different text. Animated images are compared using frames and duration; complex large animations conservatively use file-level deduplication. GIFs are never flattened for storage. Only candidates for each new image are inspected; the entire library is not decoded at every startup.

During normally triggered replies, sticker exchanges use a 95% chance after receiving a sticker, short everyday chat uses 25%, and explicit sticker requests use 100% when a suitable collection exists. Random interjections do not add images; first aid, emotional crises, and formal queries avoid random stickers. Recently sent stickers are deprioritized. Collection, deduplication, and migration notices stay in the local console rather than group announcements.

Vision disables unnecessary SDK automatic function calling (AFC), tries up to three Gemini Flash candidates, and uses a 12-second per-attempt budget, a 40-second overall budget, and a 45-second outer guard. Successful vision models are reused first. Concurrent requests for the same image share work; failures have a short cooldown. GIF recognition uses a first-frame PNG copy while collection retains the original animation. Pillow is included in requirements.

The old anonymous HF BLIP fallback is disabled; it is not repeatedly retried, and TLS verification is not disabled. If every candidate fails, no description is fabricated and collection can still proceed. Console diagnostics identify the model, exception type, timeout, or empty response. References: [Google Gen AI SDK](https://github.com/googleapis/python-genai) and [SDK empty-configuration warning](https://github.com/googleapis/python-genai/issues/2902).

`vision_models.json` independently stores successful vision priorities, actual success counts, last-success time, and latency across restarts, without changing chat model order. A newly successful model moves to the front. HTTP 429/503 errors cause temporary backoff rather than removing historical successes or probing at startup. `gemini-3.5-flash-lite` was chosen as the initial vision preference from supplied logs; historical success counts are not fabricated.

### Other private data

`people.json` stores preferred names; `google_locations.json` stores normalized locations; `moji_locations.json` stores verified page links; `bot_preferences.json` stores group modes and do-not-disturb settings; `reminders.json` stores explicitly requested reminders. JSON writes are atomic, and invalid originals are protected from overwrite. Sending waits for NapCat receipts. A timed-out personal reminder is marked as delivery unknown rather than immediately resent.

Legacy `schedule.json` lacks reliable recipients, so its data is retained but fixed-group announcements are disabled; recreate reminders with explicit recipients. The old `update_announcement.json` is disabled. A historical pre-review main-program backup is `backup/before_project_review_gemini_bot.py`.

Care references were recorded as checked on 2026-10-17: [China NHC 12356 notice](https://www.nhc.gov.cn/yzygj/c100068/202412/49a1a65386cd4be582d4702fd0926ee8.shtml), [NIMH crisis support](https://www.nimh.nih.gov/health/publications/5-action-steps-to-help-someone-having-thoughts-of-suicide), [NHS burns](https://www.nhs.uk/conditions/burns-and-scalds/), and [NHS poisoning](https://www.nhs.uk/conditions/poisoning/). The 12356 psychological support hotline has locally varying hours. For immediate emergencies in mainland China, call 120; elsewhere, use the local emergency number. The bot cannot diagnose, prescribe, or replace on-site rescue.

## Video retrieval and proxy handling

Video metadata reads detect the configured system loopback HTTP proxy, including Clash Fake-IP DNS. Set `VIDEO_SOURCE_PROXY` in private local settings to override it; missing/null uses system detection, and an empty string requests direct access. Only credential-free loopback HTTP proxies are supported. Each short-link hop is validated, with at most five redirects sharing one read time budget. The request preserves the origin Host and HTTPS tunnel target. Private addresses, HTTPS downgrades, login cookies and authentication bypasses are not allowed.

When the Bilibili API is unavailable, public page `videoData` can supply title, description, author and duration. Only JSON is parsed; no page script is executed. References to captcha support in scripts do not discard otherwise published structured video metadata; real challenge pages without video data remain failures. Comments are never invented or treated as proof of video scenes.

The NapCat log label `[card message]` does not contain a reconstructable video URL. If a particular card still cannot be summarized, provide its original public share link and the safe collection-failure label, not login information or an entire chat archive. Replies remain scoped to the quoted message and current chat.

### Explicit research and Argon answers

`ask assistant:question` uses Gemini planning, Python retrieval, an evidence-gap review and MiMo 2.6 Pro synthesis. For each planning stage the exact order is free-key `gemini-4-argon`, free-key `gemini-3.8-flash`, then (only if both failed) the paid key with Argon followed by Flash. Invalid plans and empty replies are failures. If both models on both keys fail, MiMo 2.6 Flash generates a validated English research plan. All models receive text only, with no AI browsing tools. Research has two retrieval rounds, at most six distinct queries and forty deduplicated pages. English public-page retrieval, loopback proxy support and source checks remain unchanged.

`gemini4argon answer:question` is an explicit, allowlisted Argon-only question. Mention the bot in groups; private chat accepts it directly. Free Argon is tried first, then paid Argon only after failure. This command never substitutes Flash or MiMo and does not run web research. It shares the existing duplicate-task and concurrency limits. Long replies stay within 16000 characters and are split into messages of at most 2800 characters.

The separate `GEMINI_RESEARCH_PAID_API_KEY` belongs only in local configuration or environment variables. It never replaces the primary client and is not used for ordinary chat, weather, vision, notebook organization, catalog listing or startup probes. The paid client is created lazily only for the two permitted task types, and only two exact model IDs are accepted. SDK retries are disabled. Paid input is capped at 24000 characters and output at 4096 tokens.

`GEMINI_PAID_DAILY_CALL_LIMIT` defaults to 4 attempts per Beijing calendar day (0 disables paid access; maximum 20). Attempts are reserved atomically before sending and failures still count. The private `paid_gemini_usage.sqlite3` database survives restarts and contains counts, not prompts or keys. Database errors fail closed. These are request and size limits, not a dollar spending guarantee; account credit is not queried or verified.

Explicit research has no overall five-minute deadline. Individual Gemini calls remain bounded at 600 seconds, Python retrieval processes at 360 seconds, MiMo Flash planning at 180 seconds, and MiMo Pro synthesis at 660 seconds. Ordinary Flash search retains its short-answer behavior and five-minute deadline. Source lists are available on request. No real keys or usage databases belong in the public repository.
