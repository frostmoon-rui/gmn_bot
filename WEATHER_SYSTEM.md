# Weather data and presentation contract

Weather remains demand-driven. No background loop refreshes every saved region.
Queries for the same region share an in-flight request and a ten-minute cache;
a known destination timezone also expires the cache at local midnight.

## Location resolution

- Validate the complete supplied administrative hierarchy. Do not select the
  first fuzzy result, an unrelated POI, or a namesake in another province.
- A province-only query uses its capital as an explicitly marked reference.
  Existing country/capital handling does not imply weather for an entire country.
- Domestic Amap forecasts use a verified administrative code independently of
  Google geocoding. Google, Open-Meteo and connectors consume WGS84 coordinates.
- When Google cannot resolve a domestic city, the optional `geonamescache`
  catalog can supply WGS84 coordinates. Require an exact city/alias match, a
  complete Amap administrative match, and proximity within 15 km of its city
  centroid. Ambiguous matches are rejected. Amap's GCJ-02 coordinates are never
  relabeled as WGS84 or used as the fallback forecast coordinates.
- If the catalog cannot resolve the request, the independent Open-Meteo geocoder
  remains available with the existing hierarchy checks. An unresolved location
  must not silently become another city.

The optional city catalog derives from [GeoNames](https://www.geonames.org/),
provided through the existing `geonamescache` dependency. It is a city reference,
not a promise of street-level weather observations.

## Measurements and time

- Canonical display units: Celsius, km/h, mm, snowfall cm, pressure hPa and
  visibility km. Force grades, directions, and speeds remain separate quantities.
- Reject booleans, non-finite numbers, implausible values, inverted temperature
  ranges and explicitly incompatible units. Preserve zero as a real value.
- Missing rain data, missing alert coverage and a zero precipitation probability
  mean different things. Never infer a probability from a weather description.
- Keep current observations, hourly estimates, daily minima/maxima and daily
  precipitation totals separate. A tomorrow card uses tomorrow's daily data,
  never today's current temperature as tomorrow's headline.
- Do not mix current observations from another local date into the selected day.
  Keep original provider payloads for private diagnostics. Providers that refer
  to another local date are excluded from the combined forecast.
- Hourly rows are ordered and deduplicated by their actual instants. Lower-ranked
  sources fill missing measurements only at that same instant. Unknown-zone
  naive timestamps cannot fill a known-zone timeline. Repeated DST hours remain
  distinct samples.
- Select measurements by provider priority, without averaging disagreements.
  A complete daily minimum/maximum pair takes precedence over an incomplete
  pair. Per-field provenance and forecast timestamps remain available locally.

Relevant upstream contracts:
[Open-Meteo units and time periods](https://open-meteo.com/en/docs) and
[Google Weather](https://developers.google.com/maps/documentation/weather).

## Card and diagnostics

- Show temperature, rain and wind first, then available hourly charts, daily
  charts and any hourly history. Graph units are separate; missing samples and
  missing time intervals are not drawn as measured zeroes.
- Omit empty chart sections. A lone chart occupies the row width. Source names
  do not repeat after every measurement.
- Reserve two compact footer lines for source attribution and service notices.
  Classify failures as coverage, rate limit, timeout, access, connection or data
  validation issues. Limit notice detail and summarize the remaining count.
- An alert-endpoint failure is not described as failure of all Google weather.
  Empty alert responses remain distinct from unavailable coverage and neither
  is presented as an unconditional all-clear.
- The image footer uses known provider display names and short fixed labels.
  Never include raw exception bodies, URLs, tokens, account identifiers or stack
  traces. Full endpoint statuses and raw responses stay in private diagnostics.
- Keep the existing short, localized cat-style briefing after the image. Do not
  fall back to sending the full raw weather table into a QQ conversation.

## Offline verification

Run `python -m unittest discover -s tests -q`. Weather tests use deterministic
provider fixtures, mock failures and locally rendered images. They do not start
the live bot, call models, send QQ messages, or establish real API availability.
