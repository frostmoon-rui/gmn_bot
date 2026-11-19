# Model-directed bot actions

Gemini ordinary conversations receive native function declarations generated from the validated action registry. A single native function call is converted into the same checked action used by the existing dispatcher. Text-only providers retain the validated `BOT_ACTION` JSON marker. Python dispatches it through the existing bot implementation; the model does not run code, select API keys, browse with provider tools, or choose arbitrary commands or recipients. Passive/random interjections cannot execute actions. Malformed requests are not posted as raw JSON.

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

Reminder commands accept relative delays and absolute dates. The private localized adapter accepts compact commands and a trailing relative-time suffix. Natural reminder requests without a time pass to the ordinary model for clarification. Native function arguments use explicit durations such as `10 minutes`, or `YYYY-MM-DD HH:MM` in Beijing time. The food-map action uses the current high-resolution atlas.

## Celestial environments

Use `Mars weather`, `Titan weather`, `celestial weather:Sirius` or `Kepler-22 b weather`. Gemini may call `celestial_weather` with an explicit name. All planets and commonly named moons have deterministic aliases; Gemini identifies other astronomical objects and translates numbered-moon names into English. Arbitrary celestial names use the same research path, not a three-object allowlist. Ambiguous / fictional identities are not treated as confirmed.

The ordinary free Gemini client identifies objects and extracts environmental facts from Python-retrieved NASA, ESA, observatory and selected primary-research pages. No paid-key fallback, tools or conversation history are used. Every accepted field requires a verbatim supporting passage from an actually retrieved allowlisted HTTPS source; numeric claims must also appear in that passage. Unsupported or unverified fields remain unknown. Surface / cloud-top / equilibrium temperatures, model estimates and historical observations must remain distinct. There is no invented hourly forecast or habitability claim. Source URLs, observation epochs (or unknown) and retrieval time appear in the card. No evidence produces an unknown card; verified profiles, including Sun and Moon, remain labeled reference fallbacks.

Two concurrent research jobs and two image renders are allowed. Research has bounded model / retrieval waits. Matching profiles are shared for ten minutes, failures for one minute, with bounded in-memory caches. No personal-location record is changed. Terrestrial locations continue through the original weather system after identification. The card uses measured dynamic layout so long claims and multiple sources cannot clip.

### Celestial research recovery and name meanings

The free Gemini extraction queue now advances beyond two failed candidates, remembers successful models and temporarily backs off failed ones. Short quoted entity queries retain official overview pages previously rejected by overloaded multi-field queries. Sun and Moon have NASA reference fallbacks with explicit epoch / live-data limitations. M78 is a real reflection nebula; its fictional Ultraman namesake is a separate interpretation. `celestial weather:M78` explains the distinction; `celestial weather:Land of Light` researches labeled fictional lore from first-party sources. Other fictional works should be named explicitly. No fictional data is labeled an astronomical observation. Unresolved / other meanings return clarification guidance, not invented temperatures.
