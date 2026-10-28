# Model catalogs and explicit availability checks

All five text providers use account-scoped official model endpoints. Catalog refreshes run concurrently at startup and every four hours. They fetch metadata only, never paid generation probes or QQ messages. The restricted paid Gemini backup key is not used for discovery or general health checks.

| Provider | Catalog endpoint | Official reference |
| --- | --- | --- |
| OpenAI | `https://api.openai.com/v1/models` | [Models API](https://developers.openai.com/api/reference/resources/models/methods/list) and [model lifecycle directory](https://developers.openai.com/api/docs/models/all) |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/models` | [Models API](https://ai.google.dev/api/models) |
| Claude | `https://api.anthropic.com/v1/models` | [List models](https://platform.claude.com/docs/en/api/models/list) |
| DeepSeek | `https://api.deepseek.com/v1/models` | [Lists models](https://api-docs.deepseek.com/api/list-models/) |
| MiMo | `https://api.xiaomimimo.com/v1/models` | [List models](https://mimo.mi.com/docs/en-US/api/model/list-models) |

Gemini and Claude pagination is bounded and repeated cursors fail safely. Dedicated image, ASR, audio, live, embedding, search and coding-only models do not enter ordinary chat queues. Pro/Argon remain outside ordinary Gemini Flash chat. Explicitly retired official OpenAI families are excluded using the lifecycle directory snapshot checked on 2026-10-28; this rule does not apply to a user-selected compatible endpoint. Active catalog metadata takes priority over fallback names.

`model_catalogs_cache/` stores private metadata fallback and recent explicit successful checks. Generation success is separate from catalog visibility. Confirmed successes move ahead for 24 hours without deleting other candidates; records are scoped to the endpoint and a one-way key fingerprint. A new key never inherits the previous key's verified state. Temporary 429 or timeout failures do not become permanent blacklists. Explicit balance exhaustion stops repeated same-account OpenAI retries.

When checking availability manually, use minimal text generation requests and retain HTTP codes, completion state, elapsed time and usage. A timeout or token-limited empty answer is inconclusive; a balance error says nothing about model capability. Diagnostic output, credentials and account reports must remain local. No startup generation check is enabled by this change.
