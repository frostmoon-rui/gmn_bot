# Cat Food Map

Run this service on the same computer as the QQ bot first. It uses plain HTML/CSS/JavaScript with a Python backend, requiring no Node dependency installation or separate database server. Running it does not modify CacheTide's blog or publish a website.

See [local hosting and map federation](LOCAL_NETWORK.md) for access from the same Wi-Fi, possible public hosting, and JSON exchange. The compatible public export is `/food-map/index.json`; external sources are configured in `food_map/sources.json`. Exports contain only approved local submissions, preventing imported CacheTide entries from circulating back as duplicates.

## Running locally

`python gemini_bot.py` starts the map by default. Alternatively, run `python food_map_server.py`. Open `http://127.0.0.1:8788` on the host. If a map service already occupies the port, another instance is not started; a port conflict does not stop the QQ bot.

Set `FOOD_MAP_ENABLED=0` to disable startup alongside the bot. `FOOD_MAP_PORT` defaults to `8788`; `FOOD_MAP_BIND` defaults to `127.0.0.1`, allowing access only from the host. For LAN or internet hosting, plan access controls, reverse proxying, HTTPS, rate limits, and domain/key allowlists as appropriate. Local startup does not change firewall rules, create a tunnel, or enable public listening. This loopback URL is not a remote link for group members.

## Submissions and moderation

Mention the bot in a group, or enter commands directly in private chat:

- `submit food:city|full venue name|full address|category|reason`, optionally followed by `|original HTTPS source URL`.
- `pending food`, `approve food:ID`, and `remove food:ID` are superadmin operations.
- `food map` provides local access instructions; `CacheTide blog` links to the source blog.

Ordinary submissions require review before publication; superadmin submissions are included directly. Amap must uniquely match the venue name and address, rather than selecting an arbitrary branch. Submit public restaurant addresses only, never private homes or personal contact details. Removal is a soft deletion with moderation records retained. Hidden imported duplicates are not automatically republished.

`food_map/foods.sqlite3` stores the public directory, pending submissions, and moderation records. The website exposes only allowlisted fields from published entries. It does not expose submitter QQ IDs, reviewer accounts, group IDs, private maps, weather bindings, or administrator configuration. Web APIs are read-only; submissions and reviews require verified QQ identity. The directory supports locations throughout China, but the initial import of 26 venues in eight cities does not imply nationwide coverage.

## CacheTide sources

Visit [A Little Inn by the Clouds](https://cachetide.top) and its [original food map](https://cachetide.top/food-map). The source map declares a public `/food-map/index.json` export with `schemaVersion=1` and GCJ-02 coordinates.

The initial import contains 26 sets of public place facts: name, city, public address, category, and coordinates, retaining the original article URL for each entry. Reviews, companions, photographs, and ratings are not copied. Imported venues are not presented as visits by this site's maintainer, and this site is not an official branch of the blog. The author independently manages the source blog; importing does not create two-way synchronization.

Federated sources synchronize in the background at startup, then every 24 hours by default. Configure `sync_interval_seconds` in `food_map/sources.json` between 60 and 86400 seconds. Successful results are atomically cached in the untracked `food_map/source_cache.json`. Failed requests retain cached data, including across restarts. Synchronization logs stay in the console and do not send QQ messages. Source corrections/removals appear after the next successful synchronization. Earlier manually imported local records remain under administrator control and require review before cleanup; external sources do not overwrite them.

The manual `python tools/import_food_blog.py --apply` utility remains available. It accesses the network only when explicitly invoked with the apply option; running it without arguments is offline. Prefer federation over repeated manual imports.

## Amap configuration and security

`food_map_config.py` reads the JS key and security code from the untracked local `local_settings.json`. Override them with `AMAP_JS_KEY` and `AMAP_JS_SECURITY_CODE`. The browser receives only the JS key; the security code stays in Python and is added by the fixed-upstream `/_AMapService` proxy. Configuration, SQLite files, and other bot data are not exposed by the static file service. See [Amap's JS security documentation](https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode).

The proxy permits only explicitly listed map endpoints, does not accept arbitrary upstream hosts, and rejects cross-origin requests. It uses shared caching, rate limits, and response-size limits. Public hosting still requires reverse-proxy rate limits and a domain allowlist; do not expose the development Python HTTP server directly to the internet.

The directory remains browsable when the map fails to load. There are no fabricated seed venues and no automatic collection of locations from group messages. Check JavaScript with `node --check food_map/dist/app.js`; run offline regression tests with `python -m unittest discover -s tests -q`.

The interface and status messages are English. Legacy category values from external feeds are mapped to English display labels without changing source records. Business names, city names and addresses remain source facts rather than guessed translations. The map uses the [documented Amap 2.0 English-map setting](https://lbs.amap.com/demo/javascript-api-v2/example/doc-demo/english). Missing map credentials leave the place directory usable without loading the SDK. `node tools/check_food_ui.js` checks these behaviors without network access.
