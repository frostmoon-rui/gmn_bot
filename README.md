# Lucky Cat · gmn_bot

A QQ cat companion that runs on your own computer, with chat, stickers, weather, search, and a growing food map of China.

Maintained by [frostmoon-rui](https://github.com/frostmoon-rui). The food map draws on CacheTide's [A Little Inn by the Clouds](https://cachetide.top) and [blog project](https://github.com/CacheTide/cachetide.github.io). Original sources are retained; this is an independent project, not an official branch of that blog.

## Features

| Feature | Details |
| --- | --- |
| Multiple chat providers | Gemini, OpenAI and compatible APIs, Claude, and DeepSeek; successful replies inform model priority without trying every model at startup. |
| MiMo and search | Explicit MiMo requests, background web retrieval, and concise summaries without paid model browsing tools. |
| Images and stickers | Image recognition, original animated GIF storage, near-duplicate detection, and separate vision model priorities. |
| Weather and care | Complementary weather sources, yesterday/tomorrow comparisons, an important allowlist, preferred names, and personal profiles. |
| Maps and travel | Amap places, routes, transit, traffic, and private maps; Google map and weather extensions. |
| Food map | A local website, QQ submission review, public JSON export, and automatic background synchronization of CacheTide's public JSON. |
| Group interaction | Separate group conversations, occasional repetition and interjections, video metadata summaries, help, and administrator permissions. |

See the [user guide](EXTENSIONS.md) and [weather provider notes](WEATHER_PROVIDERS.md). Earthquake information is supplementary and cannot replace official warnings or emergency broadcasts.

To recognize an ordinary photo, address the bot and attach or quote it with a question such as `Describe this image` or `What does this mean?`. Quoted images are fetched from NapCat within the same conversation, including when the bot is mentioned explicitly. Requested recognition uses the independent vision priority queue (at most three candidate models), with a question-specific cache separate from short sticker captions. Ordinary photos are not collected as stickers. Download or recognition failures are reported without asking the chat model to invent image contents; recognition replies do not receive an automatic sticker bonus.

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

## Acknowledgments

- [CacheTide](https://github.com/CacheTide/cachetide.github.io): blog design references and public food map locations. Original content belongs to its authors.
- NapCat / OneBot, Amap, Google, and the model and weather service providers.

The project has not adopted a single open-source license. Third-party material remains subject to its own terms; public access does not grant unrestricted republication rights.
