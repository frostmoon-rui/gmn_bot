# Global community maps and the shallow help guide

Use `heatmap china` or `heatmap world` (mention the bot in groups; private chats
work directly). Maps include **all recognizable saved regions by default across
chats**, deduplicated by account. There is no enrollment or minimum crowd size.
A single-member province or country is displayed. Only the requesting chat gets
the image; there is no broadcast or group membership request.

`heatmap leave` excludes yourself globally. `heatmap join` restores participation;
`heatmap join Sichuan` or `heatmap join France` sets your own map-only coarse region
without changing your weather binding. Models can request maps but cannot change
another account's preferences. `heatmap privacy` explains the current rules.

The image contains only provinces/countries and counts, never member names,
account IDs, cities, addresses or personal coordinates. Missing or ambiguous
regions are skipped rather than guessed. Existing local geocoding metadata is
reused without contacting a geocoding API. Newly saved regions appear on the next
map automatically. Consistent legacy manual entries remain usable, once per
account; current weather bindings take precedence over old group-specific entries.

Global self preferences are added under `_global` in the private
`group_heatmap.json` file. Old group entries are preserved, not rewritten into
weather or personal profiles. Neither the settings nor bindings may be committed.
Opt-out affects newly generated images, not images already sent.

Rendering receives count pairs only and uses offline Natural Earth boundaries.
Images are not collected as stickers. Small islands may be simplified.

`help` opens eight visual categories. `help CATEGORY` shows the entire category
without a third menu level; `help text CATEGORY` returns copyable text. Old topic
aliases and existing weather, maps, research, care and admin functions remain.

## Earthquake sources and cadence

Official post-event reports come from the China Earthquake Administration's
[China Earthquake Networks Center](https://www.ceic.ac.cn/), using its public
`https://www.ceic.ac.cn/data/data.json` bulletin. Reports refresh every **1800
seconds**, including empty responses/failures. Concurrent queries share the same
refresh. Wolfx remains a clearly labelled fallback; it is not described as an
official direct delivery channel. Source changes retain legacy delivery IDs to
avoid repeated subscriber notifications.

Existing CENC/Sichuan warning relays remain separate from this 30-minute bulletin
schedule. Their short polling interval and explicit subscriber boundaries remain;
there is no new all-group announcement. Cached event ages are recalculated.
Post-event bulletins are **not advance warnings**. Missing or unavailable warnings
do not establish safety. Follow local official instructions; the bot provides no
reliable earthquake countdown. Endpoint correctness is covered by offline fixtures,
not a guarantee of live provider availability.
