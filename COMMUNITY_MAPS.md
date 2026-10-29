# Community maps and the shallow help guide

Mention the bot in a group, then use:

| Command | Result |
| --- | --- |
| `heatmap join Sichuan` | Opt yourself into province-level statistics in this group |
| `heatmap join France` | Opt yourself into country-level statistics in this group |
| `heatmap join` | Attempt coarse extraction from your own existing weather binding |
| `heatmap leave` | Withdraw yourself from future maps in this group |
| `heatmap china` / `heatmap world` | Draw the current group's aggregated map |
| `heatmap privacy` | Explain consent and publication rules |

Weather bindings are **not automatically enrolled**. There is no administrator
command to enroll another member. Each draw verifies the current OneBot member
list; a failed verification produces no map. Members who left are excluded.
No personal addresses are geocoded for this feature. Ambiguous places require an
explicit province or country rather than guessing. Stored participation contains
only coarse region codes and a consent time, scoped by group/member identifiers.
`group_heatmap.json` is private runtime data and must never be committed.

Every region with a consenting current member is published, **including a single participant**.
The renderer receives region/count pairs, never names, account IDs, addresses or
coordinates. No list of excluded members or exact excluded total is disclosed. Grey
means unpublished, not necessarily zero. This reduces disclosure, but aggregate
statistics are not a promise of perfect anonymity. Previously sent images cannot
be erased merely by withdrawing consent; withdrawal affects newly generated maps.

Maps render with Pillow and vendored public-domain Natural Earth outlines; no
member data is sent to a map API. The image includes a region/count list. Small
islands may be simplified. Images are not added to the sticker collection.

`help` opens eight visual categories. `help CATEGORY` contains the entire category
on one image, without requiring a third menu level. Old topic commands remain
aliases; `help text CATEGORY` returns copyable text. Rendering failures fall back
to text. Existing weather, maps, research, care and administration commands remain.

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
