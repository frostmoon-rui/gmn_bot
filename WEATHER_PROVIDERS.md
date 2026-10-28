# Complementary weather sources

## Detailed forecasts and complete diagnostics

QQ images and briefings omit field attribution, provider names and endpoint diagnostics. Weather values, warnings and disagreements remain visible. Complete local JSON retains provenance; `format_weather(..., diagnostics=True)` explicitly includes diagnostic details. Briefings use the caring cat persona and reject leaked style notes or reasoning parts.

Explicit weather, morning greetings, tomorrow forecasts, the existing 22:30 important-user report and Amap weather commands now use deterministic detailed formatting instead of short model summaries. Reports include available temperature/feels-like/dew point, humidity, wind/gusts/force, pressure, visibility, precipitation, UV, seven-day forecasts, 48 forecast hours, 24 history hours, conflicts and available alerts. Unknowns are not zero; model history is not station observations; cloud cover is not satellite imagery or measured lightning. Bound places, coordinates, alert areas and raw payloads never go to QQ. The complete report is rendered locally as one weather dashboard PNG, followed by a short Gemini briefing. Only the image mentions the requester. Pillow uses installed system fonts; no browser is required. The briefing uses the regular Gemini client, at most three models with a 12-second timeout each, no chat history and no paid-key fallback. Rendering or model failure falls back to a short text summary rather than flooding the chat with tables.

Google endpoints retain partial results and follow bounded pagination for days, hours, history and alerts. Alert-only responses survive forecast failures. All provider payloads and unknown daily fields are retained for local audits. Dates and timestamps use IANA zones; Windows needs the included `tzdata` dependency. Existing on-demand fetching, ten-minute caches and provider backoff remain; complete-response caches are capped at 32 areas.

Run `python tools/check_weather_multi.py --live --all --output weather_diagnostics/latest.json` for explicit live checks of public Shanghai/Tokyo samples. This does not start the bot, inspect personal bindings or send QQ messages. The complete sanitized JSON includes raw responses, normalized providers, endpoint statuses, merged values and a detailed report; `--full` also prints it. Secrets are masked and exports are excluded from Git. Missing MSN/Moji API bridge configuration is reported honestly as unconfigured.

On 2026-10-26, Shanghai returned Amap, Moji and Open-Meteo data; Google current/days/hours/history responded while alerts returned 404. Tokyo returned Open-Meteo data and a Google alert response, while Google's weather fields returned 404. These are sample observations, not permanent coverage guarantees.

References: [Google hourly pagination](https://developers.google.com/maps/documentation/weather/reference/rest/v1/forecast.hours/lookup), [daily pagination](https://developers.google.com/maps/documentation/weather/daily-forecast), [public alerts](https://developers.google.com/maps/documentation/weather/reference/rest/v1/publicAlerts/lookup), [Open-Meteo variables](https://open-meteo.com/en/docs).


Weather collection runs in parallel and fills individual missing fields instead of stopping at the first successful provider. The legacy `google_weather()` entry point delegates to the aggregator in `weather_providers.py`.

## Providers

- **Amap:** supplies current conditions, humidity, wind direction/force, and multiday forecasts in China when a key is configured. Beijing, Tianjin, Shanghai, and Chongqing are accepted even when the API classifies them at province level.
- **Google Weather:** uses a Google key and geocoding, with parallel requests for current, hourly, daily, historical hourly, and alert data. A failed component does not discard successful ones. If both current and daily requests explicitly return 404 for a location in China, the process avoids repeatedly querying that unsupported location; other providers continue normally.
- **Moji public pages:** accepted only after checking the location title. Retrieval respects robots rules, uses low-frequency caching, and stops at verification or rate limits without cookies or bypasses. An authorized API adapter can replace page retrieval.
- **Open-Meteo:** requires no user key and supplies apparent temperature, UV, wind speed, humidity, hourly precipitation, cloud cover, and model estimates for past days. It uses WGS84 coordinates, never unconverted Amap GCJ-02 coordinates. If Google geocoding fails, a unique exact match from Open-Meteo's place search can supply coordinates; ambiguous matches are not guessed.
- **MSN Weather:** an adapter integration point exists, but it is not connected until configured. Microsoft's documented MSN Weather connector is for products such as Power Automate and Logic Apps, not a generic Python API key already available to this bot. Provide an authorized HTTPS adapter and sample response. Azure Maps Weather is a separate service requiring its own account permissions and integration.

Valid Amap/Google fields take priority; other sources fill gaps. Results include `sources`, `field_sources`, `provider_status`, `source_reports`, and `disagreements`, exposing each field's provider, update time, and observation/model status to the summarizer. Only matching local dates are merged. Unknown values, invalid ranges, and NaN are rejected; valid zero values such as UV 0 or 0% precipitation probability are retained. Conflicting numbers are not averaged, wind-force levels are not converted into definite speeds, and modeled history is not presented as station observations.

Each provider has a 65-second budget for paginated detail. Concurrent requests for the same location share collection, successful results are cached for 10 minutes, and failed providers back off for 5 minutes.

## Authorized MSN and Moji adapters

These environment variables enable adapters in parallel collection without code changes:

~~~text
MSN_WEATHER_BRIDGE_URL=https://weather-adapter.example.com/msn
MSN_WEATHER_BRIDGE_TOKEN=optional-bearer-token
MOJI_WEATHER_BRIDGE_URL=https://weather-adapter.example.com/moji
MOJI_WEATHER_BRIDGE_TOKEN=optional-bearer-token
~~~

The backend sends a POST request with `latitude`, `longitude` (WGS84), and `units="Metric"`. Responses use standard fields in Celsius and km/h and must include the actual local date in `local_date`. Omit unavailable fields rather than inventing zero-valued observations. Example:

~~~json
{
  "local_date": "2026-10-18",
  "report_time": "2026-10-18T12:00:00+08:00",
  "desc": "Overcast",
  "temp": 18,
  "feels_like": 17,
  "wind_kmph": 9,
  "uv": 2,
  "days": [
    {"date": "2026-10-18", "minTemperature": {"degrees": 14}, "maxTemperature": {"degrees": 20}},
    {"date": "2026-10-19", "minTemperature": {"degrees": 13}, "maxTemperature": {"degrees": 19}, "precipitationProbability": 60}
  ]
}
~~~

This illustrates the format, not a current forecast. Integration also needs authentication details, API documentation, authorization/quota information, and a sample response. There is no need to purchase an unspecified "MSN key" first.

## Maintenance

`python tools/check_weather_multi.py --live` makes a small number of network requests using public Shanghai city coordinates, existing API quota, and public fallback sources. It does not start the bot, use members' locations, change bindings, or send QQ messages. The previously recorded check on 2026-10-18 combined Amap, Moji public pages, and Open-Meteo successfully; Google's 404 for those coordinates was isolated correctly. This is a historical check, not a guarantee of current availability.

References: [Google Weather](https://developers.google.com/maps/documentation/weather/overview), [Open-Meteo](https://open-meteo.com/en/docs), [MSN Weather connector](https://learn.microsoft.com/en-us/connectors/msnweather/), and [Azure Maps Weather concepts](https://learn.microsoft.com/en-us/azure/azure-maps/weather-services-concepts).

Weather images now emphasize temperature, rain and wind with large summary cards. Unit-separated line charts show hourly forecasts first, daily forecasts second, and recent history last when available. Missing samples leave gaps; probabilities and precipitation totals never share an axis. Rendering is local with Pillow.

Model or rendering failures now produce only a short friendly notice, never a raw report excerpt. Empty half-hour maintenance logs are suppressed; real model refreshes and failures remain logged.
