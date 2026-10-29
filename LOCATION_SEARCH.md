# Location integrity and current local-service searches

Location bindings change only through an explicit `set location` command. Both `set location:ACCOUNT_ID PLACE` and `set location:PLACE ACCOUNT_ID` are supported; editing another account still requires the original admin. Model `REGION_UPDATE` tags are ignored and removed from visible replies. Weather, travel, heating and research questions never change a home binding.

Google city results marked `partial_match` require a complete match of the supplied city and administrative components, rather than blanket acceptance or rejection. A localized second query can verify a Chinese city request; the English locality is retained only when both results have the same place ID. Unmatched street data and ambiguous cities are rejected. Cached geocodes are versioned and revalidated on demand. The free fallback extracts the city from an administrative address without truncating multiword city names. Domestic providers verify country scope before contributing weather.

Current/local-service searches use web sources instead of requiring an encyclopedia article. `latest` queries skip Wikipedia, retain local-language place names and retrieve government results in addition to ordinary searches. Google, DuckDuckGo and Bing result redirects are decoded before public-page URL validation. Government links are prioritized before the crawl limit; missing Wikipedia matches do not invalidate useful web pages. The summarizer must check jurisdiction, publication dates, standing seasonal rules and current-year changes rather than assume one city's schedule applies everywhere.

Search diagnostics report counts for Google links, fallback links, Wikipedia pages and fetched pages. No CAPTCHA, login, TLS or public-address validation is bypassed. Empty or blocked search channels remain failures, not evidence that the requested information does not exist.

Search engines contribute organic result headings and snippets rather than every external anchor in their HTML. Topic overlap is checked before a fallback engine is considered successful and again against fetched text. A response full of unrelated people/profile links does not stop fallback or enter the summary. Chinese queries request a Chinese search locale and may retry a concise keyword form; all retries retain the original query as their relevance anchor. Diagnostics include the actual query, engine, candidate/accepted/rejected counts, rejected bodies and transport failures. Lexical overlap is only a conservative relevance screen, not a claim of factual accuracy; synonyms and cross-language evidence can still require a revised query. No private queries are committed to source control.

The parser also supports Google ARIA headings and DuckDuckGo Lite anchors. HTTP result targets can be retried through HTTPS with normal TLS and public-address checks; plaintext requests are not enabled. Chinese fallback additionally tries Baidu, within a shared 75-second admission budget. Captcha/consent pages are recorded and paused instead of treated as empty successful searches. Diagnostics retain a maximum of three rejected title/host samples with matched/missing terms, plus HTML size and anchor counts, without dumping full response bodies. Concise retries may omit the year to find standing regulations; the final report must still verify current-year changes and each jurisdiction rather than present an old notice as current.

Chinese relevance uses the pinned local Jieba tokenizer, with calendar years removed before segmentation. It does not build artificial pairs across adjacent words such as a year suffix and a province name. Missing dependencies are installed through requirements, never silently replaced with the old bigram algorithm.

A Google JavaScript shell can be rendered once in an isolated, sandboxed Playwright browser, using the same SEARCH_PROXY policy as HTTP retrieval. This optional path uses no existing user profile, stealth configuration, login, or CAPTCHA solver. It admits only bounded GET requests to fixed search/asset hosts, validates public targets and disables downloads and service workers. Install optional browser support with `python -m pip install playwright` and `python -m playwright install chromium`. Verification pages remain failures. Script/consent/verification responses stop further Google requests in that round; an empty result caused by channel restrictions is reported as an access problem rather than an absence of information.

When a current-events search has no usable web pages, a bounded Google News public RSS lookup can discover up to three publisher articles. Feed titles alone never become article evidence. An isolated browser follows normal news redirects to the named publisher, and the final public HTTPS publisher URL, full text and topic overlap are checked before adding the page. The news redirect service's normal data request is permitted only on its specific Google News endpoint; publisher POST requests remain blocked. Feed publication time stays separate from article/event dates. The fallback does not invoke paid AI search, reuse a personal browser session or change proxy settings. News is an additional retrieval channel, not a claim that all academic or current questions belong in news.

County and prefecture weather requests retain the complete supplied hierarchy for
validation, while extracting the leaf city/district for fallback searches. Google
administrative levels 4 and 5 are accepted only when all supplied components
match. Amap weather independently validates its administrative geocode and weather
code; Google geocoding failure does not block it. A domestic POI is not accepted
as city weather for a foreign place name. No GCJ-02 coordinates are relabeled as
WGS84 in this fallback.

An account-only `set location:123456789` uses an explicit weather location from
that same speaker and conversation within ten minutes. Other users, other chats,
expired context and bound-home default queries cannot supply that location.
Without matching context, the command requests a city instead of storing a
numeric account as the location. Existing permission checks still apply when
binding another account. Weather provider outages no longer ask the user to
resupply an already complete address as if it were necessarily incomplete.
