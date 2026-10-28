# Model-directed bot actions

The normal conversation model can return one validated `BOT_ACTION` JSON marker. Python dispatches it through the existing bot implementation; the model does not run code, select API keys, browse with provider tools, or choose arbitrary commands or recipients. Passive/random interjections cannot execute actions. Malformed requests are not posted as raw JSON.

Available actions: weather, ordinary search, detailed research, public website screenshots, read-only map/place/route/traffic queries, video summaries, image generation, saved stickers, help, status, earthquake reports, food map screenshots, recent sources, and the current speaker's reminders. Existing native commands remain available, including administrative commands; they are not arbitrary model tools. Existing whitelist, ownership, queue and rate checks remain in force. Detailed research still uses the configured scoped planning policy and MiMo synthesis.

Weather requests extract an explicit place before consulting a saved home location. For example, `weather forecast Madagascar`, `Madagascar weather forecast` and `weather in Madagascar` are one-off lookups, not location-binding updates. The private localized runtime also accepts Chinese word orders through a separate adapter. More conversational requests can be interpreted by the model.

Google geocoding distinguishes countries from cities. A country-only request uses the local GeoNames country dataset via [geonamescache](https://github.com/yaph/geonamescache), followed by geocoding that capital and verifying the country code again. This does not add a country-data API or credentials. Cards and model facts explicitly label this as a capital reference, not nationwide weather. Ambiguous countries/capitals, missing data or failed geocoding require a more precise city rather than a guessed coordinate. Temporary lookups never rewrite saved home bindings.

Examples of internal model outputs:

```text
[BOT_ACTION: {"tool":"weather","args":{"location":"Madagascar","target":"tomorrow"}}]
[BOT_ACTION: {"tool":"research","args":{"query":"Compare the evidence for these explanations"}}]
[BOT_ACTION: {"tool":"website","args":{"page":"food","language":"en","theme":"dark"}}]
```

These markers are model output, not user commands. Only the current event supplies the actor, group, privilege flags and reply context. One action is executed per model turn; the model cannot create tool loops or use unread/quoted content as authorization.
