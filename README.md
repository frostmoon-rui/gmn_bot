# Lucky Cat · gmn_bot

![Lucky Cat — QQ chat, weather, and a food atlas](docs/project-cover.svg)

A local QQ cat companion with chat, stickers, weather, research, maps, music requests, and audio separation.

Maintained by [frostmoon-rui](https://github.com/frostmoon-rui). The food map draws on CacheTide's [A Little Inn by the Clouds](https://cachetide.top) and [blog project](https://github.com/CacheTide/cachetide.github.io). Original sources are retained; this is an independent project, not an official branch of that blog.


## November 25, 2026 update

- Music requests can use official QQ Music song search and browser playback. VIP previews show the measured duration. Vocal and accompaniment separation runs locally.
- Supported background jobs send a single progress notice after 30 seconds if still running. Daily maintenance reclaims expired music caches and abandoned media workspaces.

## Features

| Feature | Details |
| --- | --- |
| Multiple chat providers | Gemini, OpenAI and compatible APIs, Claude, and DeepSeek; successful replies inform model priority without trying every model at startup. |
| Natural-language actions | Address the bot to use existing tools for weather, maps, reminders, research, music selection, audio processing, and other supported requests. Audio links must come from the current message; quoted attachments use the current reply. |
| Drawing | Pollinations first, with Gemini 3.1 Flash Lite Image as a fallback when the paid Gemini key is configured. Fallback attempts share the daily paid call limit with research and advanced model commands. |
| Research and search | Gemini planning, Python retrieval, MiMo 2.6 Pro synthesis, and explicit Argon answers with narrowly scoped paid-key fallback. |
| Images and stickers | Image recognition, original animated GIF storage, near-duplicate detection, and separate vision model priorities. |
| Weather and care | Complementary weather sources, yesterday/tomorrow comparisons, an important allowlist, preferred names, and personal profiles. |
| Maps and travel | Amap places, routes, transit, traffic, and private maps; Google map and weather extensions. |
| Food map | A local website, QQ submission review, public JSON export, and automatic background synchronization of CacheTide's public JSON. |
| Group interaction | Separate group conversations, occasional repetition and interjections, video metadata summaries, help, and administrator permissions. |

See the [user guide](EXTENSIONS.md) and [weather provider notes](WEATHER_PROVIDERS.md) and [weather data/display contract](WEATHER_SYSTEM.md). Earthquake information is supplementary and cannot replace official warnings or emergency broadcasts.

To recognize an ordinary photo, address the bot and attach or quote it with a question such as `Describe this image` or `What does this mean?`. Quoted images are fetched from NapCat within the same conversation, including when the bot is mentioned explicitly. Requested recognition uses the independent vision priority queue (at most three candidate models), with a question-specific cache separate from short sticker captions. Ordinary photos are not collected as stickers. Download or recognition failures are reported without asking the chat model to invent image contents; recognition replies do not receive an automatic sticker bonus.

Reply to a bot-sent image, mention the bot and send exactly `delete` to retract it and persistently block the image from future collection or replies. Any member can correct an image; no user is blacklisted. See [image correction and recall limits](STICKER_CORRECTION.md).

## Getting started

### Keep the public source separate from an existing local bot

This public checkout uses English commands and replies. It is not a backup of private bot data or a localized runtime. Updating it does not restore personal weather bindings, profiles, notebook entries, or stickers.

When preserving a Chinese or multilingual installation, keep two independent directories: the local runtime (including its private data) and a clean Git checkout for public development. Do not unpack a source ZIP over the runtime without reviewing language behavior and making a recoverable backup first. A source-only ZIP does not include private JSON or SQLite files. Never upload a runtime backup to GitHub, and never replace newer records with an older backup merely to change the interface language.

Run `python -m unittest discover -s tests -q` in each directory after changes. Do not run both configured bot copies simultaneously against the same QQ account or data directory.

Use Python 3.10 or later (3.12 recommended) and a configured NapCat / OneBot v11 forward WebSocket connection. Follow platform rules and obtain members' consent for stored information and automatic replies.

~~~powershell
git clone https://github.com/frostmoon-rui/gmn_bot.git
cd gmn_bot
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item local_settings.example.json local_settings.json
~~~

Edit `local_settings.json` with your Gemini key, `MASTER_QQ`, and credentials for services you want to enable. Other services may remain blank. Preserve existing local configuration. Environment variables override local settings with the same name; endpoint settings such as `OPENAI_BASE_URL` use environment variables. The program does not automatically load `.env` files.

The NapCat WebSocket address is `WS_URL` in `gemini_bot.py`, defaulting to `ws://127.0.0.1:3001`. Run it on a trusted host; never expose an unauthenticated WebSocket to the internet. Once configured, start the bot:

~~~powershell
.\.venv\Scripts\python.exe gemini_bot.py
~~~

The first run creates required local data files. Configure the administrator and API credentials before connecting a fresh checkout to QQ. Manual probes `test_image_flow.py` and `test_imagen.py` may incur API charges or send QQ messages; they are not offline tests.

## Cat Food Map

The map starts with the bot, or independently:

~~~powershell
python food_map_server.py
~~~

Open `http://127.0.0.1:8788`. No Node build is required. Set `AMAP_JS_KEY` and `AMAP_JS_SECURITY_CODE` for Amap; the security code stays on the server.

Mention the bot in a group and use `submit food:city|full venue name|full address|category|reason`. Ordinary submissions become public after superadmin review. The website is read-only and does not publish submitter QQ IDs or private profiles.

### Automatic synchronization with CacheTide

`food_map/sources.json` includes `https://cachetide.top/food-map/index.json`. The map service synchronizes in the background at startup, then every 24 hours by default. Opening the website is not required. Set `sync_interval_seconds` between 60 and 86400 to change the interval. Failed requests retain the last successful cache, including across restarts. Synchronization does not change CacheTide's blog or write to its repository.

Only public place fields are synchronized. Entries are deduplicated by city, name, and coordinates; local submissions take priority, and hidden entries are not automatically republished. External corrections or removals appear after the next successful synchronization. Earlier manually imported local entries require separate administrator maintenance.

The `/food-map/index.json` export is compatible with `valaxy-addon-food-map`, using `schemaVersion: 1`, `coordinateSystem: "GCJ-02"`, and `spots`. It contains only this site's published local content, preventing external entries from circulating back to their source. Other sites can subscribe using your public HTTPS export URL.

Deployment is local by default. `127.0.0.1` is not a public link for group members, and pushing to GitHub does not host the Python service. See the [local hosting guide](food_map/LOCAL_NETWORK.md) and [map documentation](food_map/README.md).

## Project layout

~~~text
gemini_bot.py          QQ connection, chat, and model routing
bot_extensions.py     Extension entry points
bot_runtime.py        Atomic storage, receipts, and per-chat scheduling
local_settings.py     Local configuration; real settings stay private
food_map.py           Submissions, moderation, and the public directory
food_sources.py       Background JSON federation and caching
food_map_server.py    Local map HTTP server
food_map/dist/        Plain HTML, CSS, and JavaScript frontend
weather_*.py          Weather aggregation, history, and wording
tests/                Offline regression tests
tools/                Maintenance and manual import utilities
~~~

## Checks and maintenance

~~~powershell
python -m unittest discover -s tests -q
python check_project.py
node --check food_map/dist/app.js
~~~

Node is only needed for the optional JavaScript syntax check. Offline tests need no real model credentials. Do not import or start the main program merely to test it.

## Uploads and privacy

The repository contains application source, frontend files, tests, and example configuration. Real API keys, QQ/contact lists, personal profiles, chat notebooks, location bindings, sticker libraries, SQLite databases, runtime caches, and historical backups stay on the deployment computer and are excluded by `.gitignore`. Back up private data before updates. Do not upload an archive of the entire working directory in place of a reviewed Git commit.

Before public deployment, configure HTTPS, rate limits, Amap domain allowlists, access controls, and provider billing. The web service exposes fixed routes; never serve the entire bot directory as a static root. Model names and capabilities depend on your account; listed models are not guaranteed to be available.

## Public website integration

Use `website screenshot:friends en dark`, `recommended links`, or `friend application` (mention the bot in groups). Capture is on demand, uses a fresh sandboxed browser context and replies only to the requesting chat. No admin session, private queue or local bot data is shared. See [optional screenshot setup](WEBSITE_SCREENSHOTS.md). The website's public route/resource manifest is at `https://frostmoonrui.com/data/bot-site.json`; private review operations stay in the website owner console.

## Acknowledgments

- [CacheTide](https://github.com/CacheTide/cachetide.github.io): blog design references and public food map locations. Original content belongs to its authors.
- NapCat / OneBot, Amap, Google, and the model and weather service providers.

The project has not adopted a single open-source license. Third-party material remains subject to its own terms; public access does not grant unrestricted republication rights.

## Local audio separation

Reply to an uploaded audio file, a video with audio, a QQ voice message, public direct media link or downloadable audio page with `separate audio`. You can also send `separate audio: URL` or put the command in an attachment caption. For a native QQ video, the bot can use the local video cache, the file returned by NapCat, or the QQ playback URL. It returns separate MP3 files for vocals and accompaniment. Add `voice` after the command for two additional QQ voice messages if the input is at most three minutes.

- Set `MEDIA_SPLEETER_EXE` to an absolute path to [SpleeterMsvcExe](https://github.com/wudicgi/SpleeterMsvcExe/releases) with a working `2stems` model. Keep the model and required libraries beside the executable. Set `MEDIA_FFMPEG_EXE` to an [FFmpeg](https://ffmpeg.org/download.html) executable with the LAME MP3 encoder.
- For SILK voice input, install Node.js and run `npm install --ignore-scripts --prefix PATH silk-wasm@3.7.1` in a local tools directory. Set `MEDIA_SILK_MODULE` to the absolute path to `PATH/node_modules/silk-wasm/lib/index.cjs`.
- The bot checks for a decodable audio track first. It accepts up to 80 MiB and 10 minutes per job. One job runs at a time, with up to three active or queued jobs. Files are processed locally.
- URL inputs must point to downloadable audio or video over public HTTP or HTTPS. Redirects are validated. If the system resolver only returns synthetic proxy addresses, public DNS resolution uses Cloudflare DNS over HTTPS. Streaming playlists and player pages are outside this input format.
- `/kugou music SONG ARTIST` produces an official Kugou search link. Playback and subscription entitlements are handled on Kugou.
- `/play music SONG - ARTIST` tries a unique QQ Music match first when browser capture is configured. The download fallback searches Openverse, Wikimedia Commons, and eligible single-recording Internet Archive items. Exact title and version matches take priority. With an artist specified, a high-confidence title variation of the same recording type may be sent and is labelled as approximate. For same-title recordings, mention the bot with the artist within 10 minutes, or reply with the artist in private chat. You can also use `/get track NUMBER` to choose a numbered result. The bot keeps these choices separate by account, chat, and requester. A unique recording is sent automatically, with alternate download URLs tried silently if the first fails. If multiple matches remain, `/get track 1` selects a result in the same chat within 10 minutes. The bot sends one credit message, the full MP3 and one QQ voice preview of up to 60 seconds. For VIP preview-only recordings, the MP3 and voice both contain the same available preview and the bot labels it. Following an automatic match, /get track 1 repeats the result for 10 minutes. Add file to send just the MP3. `/kugou music SONG` and `/netease music SONG` open the platforms’ search pages. Preview links point to matching QQ Music, NetEase Cloud Music, or Kugou recordings when available. Different versions or performers are labelled individually. Known platform links can be saved in private local settings under `MUSIC_PREVIEW_CATALOG`, as entries with `title`, `creator`, `url`, and an optional list of `aliases`. For a downloadable Jamendo result whose direct media fails, Live Stream Downloader 0.5.7 or later can detect the same track in a temporary Chromium profile. This fallback uses the extension installation from Edge or Chrome and Chromium installed with `python -m playwright install chromium`.
- To enable official QQ Music song search, get a key from the [QQ Music Skills page](https://y.qq.com/n/ryqq_v2/qqmusic_skills) and set `QQMUSIC_API_KEY` in private local settings or the bot environment. For official NetEase song search, install [ncm-cli](https://github.com/NetEase/skills/tree/master/ncm-cli-setup), configure it under the Windows account that runs the bot, and set `NCM_CLI_JS` to the absolute path of its `dist/index.js`. On Windows the bot also checks the per-user install under `%LOCALAPPDATA%/GMN-Bot/ncm-cli`. The search adapters send the requested song and artist to these services and return official track links. Audio delivery continues to use the authorized download sources. Restart the bot after changing local settings.
- For QQ Music browser audio on Windows, install Playwright Chromium and Live Stream Downloader 0.5.7 or later in Chrome or Edge. Run `python -m music_capture`, sign in to QQ Music in the dedicated window, then close it. Set `QQMUSIC_BROWSER_CAPTURE` to `true` in private local settings and restart. For a unique title and artist match, the bot tries the media served by the official player first, including MP3 and M4A. Duplicate catalog entries are tried as alternate sources for the same recording. The bot labels previews and caps them at 60 seconds. It verifies full-length streams against the track duration and uses the existing download catalogs when playback or capture fails. Sign in again using the same command if the session expires. The dedicated browser data directory stays in `%LOCALAPPDATA%/GMN-Bot/qqmusic-automation`, and temporary audio files are cleaned after delivery.
- `/download audio URL` looks for audio in a public file, HTML audio or video player (including publicly published media metadata), audio download link, podcast feed, or unencrypted HLS recording. The bot tries up to three candidates and replies with a full MP3 plus complete QQ voice playback in parts up to two minutes. Files are limited to 80 MiB and ten minutes. This server-side path supports publicly downloadable files. HLS supports completed recordings with discrete MPEG-TS, AAC, or fragmented MP4 segments. The bot selects an audio rendition when one is available. DASH, encrypted HLS, active live streams, and byte-range playlists need a separate input format. To use [Bulk Media Downloader](https://github.com/inbasic/bulk-media-downloader), filter discovered resources to audio, copy a public file URL, and pass it to the same command. With [Live Stream Downloader](https://github.com/chandler-stimson/live-stream-downloader), use the public .m3u8 playlist URL captured by the extension. If the URL requires the browser session, save the file with the extension, upload it to QQ, and reply to it with `/download audio`.
- Audio jobs remove temporary downloads, conversions, and voice files when they finish. Daily maintenance also recovers interrupted job folders older than 24 hours.

## Community maps and visual help

Use `heatmap china/world` after mentioning the bot in a group, or directly in a private chat. All recognizable saved regions are included by default across chats, once per account; single-member regions are displayed. No individual locations or member list is published. `heatmap leave` excludes yourself globally; `heatmap join` restores participation. Existing saved weather/profile records are preserved.

`help` opens eight visual categories; `help CATEGORY` stays on one category page, and `help text CATEGORY` keeps commands copyable. Existing topic aliases remain. Official CENC bulletins refresh every 30 minutes; warning/subscription channels remain separate. See [map rules and source details](COMMUNITY_MAPS.md).

Celestial weather: `Sun weather`, `Moon weather`, `celestial weather:M78`, or `celestial weather:Land of Light`. See the [user guide](EXTENSIONS.md) for scientific versus fictional modes and album video backup. [Current model information](MODEL_UPDATES.md) documents the explicit catalog refresh and generation-access limits.

## On-demand autonomy development

From this checkout on Windows, run `python tools/autonomy_dev.py --version X.Y.Z`.
The helper creates clean copies of the public autonomy and host repositories, reads
the Gemini quota before and after one Hydrogen High call, and stops on an invalid
quota response or a five-hour or weekly reading below 30%. The Windows process
lock prevents overlapping jobs from this account. Quota readings are observations
and can include activity outside the job.

Antigravity receives tracked public Python source, tests and package documentation.
It returns scoped replacements and a self-review. The runner executes the fixed
package and host test commands in disposable checkouts with the candidate on the
import path. Only an approved, tested patch can be committed and pushed with its
tag in one Git operation. Use
`--hold-release` to archive a reviewed candidate without pushing.

This command uses the authenticated Antigravity and Codex CLIs, Git credentials,
Python test dependencies, and a running local NapCat connection to send an owner
update. Set `--runtime` if the private runtime is outside the usual sibling folder.
The optional `--notice-config` reads a local JSON file with the `language`,
`no_proposal`, `released`, `stopped`, `unknown` and `format` string keys from
`DEFAULT_NOTICE`. Run records and QQ receipts are stored under
`%LOCALAPPDATA%/GMN/autonomy_dev_logs`. The command runs once and exits.

The social reviewer may add catalog-only signals to the local SQLite desire pool.
Each external person contributes at most one vote per topic per day. Development
receives aggregate counts and relationship tiers. The model may decline the
ranked ideas and choose an intrinsic goal.

Development runs both suites in clean checkouts before a separate Antigravity
boundary audit. It may correct the candidate twice before that audit passes.
Codex receives only the approved diff, brief motivation and test summary. An
explicit Codex rejection allows one additional Antigravity repair and a second
Codex decision. Each Antigravity call uses the same quota checks. Pass
`--bot-account` to select which local account contributes signals when the
database has multiple accounts. `--bootstrap-patch` starts from an inspected
local patch for a controlled rehearsal.

Unpublished candidates are checkpointed before verification. Re-running the
same version on the same autonomy base resumes the latest stopped checkpoint
and locks its selected topic. Start with `--fresh` to choose a new topic, or use
`--resume-from-patch PATH` to select a particular checkpoint directory or patch.
The pipeline reruns all checks on the resumed candidate.

Opus availability is measured through the shared Claude/GPT quota group. The
report records both groups, attempted models and successful model calls. It
shows quota changes only within the same refresh window.
