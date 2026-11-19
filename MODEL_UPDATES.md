# Model information · 2026-11-19

Maintained by CacheTide. Discovery is separate from successful generation; keys, quotas and paid access remain unchanged.

| Provider | Current model information | Account text candidates refreshed |
| --- | --- | ---: |
| Google | Gemini 4 Neon is newly listed; Hydrogen, Argon, Carbon and Flash families remain available as candidates. | 19 |
| OpenAI | Current flagship directory includes GPT-6.2 Astra / Sol, GPT-6.1 Terra and GPT-6 Luna. | 47 |
| Anthropic | Claude Fable 5.2, Opus 5.5, Sonnet 5.5 and Haiku 5.5 are in the current directory. | 15 |
| DeepSeek | `deepseek-flash` routes to V4.1 Flash; `deepseek-v4-pro` remains the Pro API ID. | 2 |
| Xiaomi | MiMo 2.6 Pro, Pro Ultraspeed and Flash remain the current discovered text routes. | 3 |

Official documentation: [Gemini models](https://aistudio.google.com/docs/models), [OpenAI models](https://developers.openai.com/api/docs/models), [Claude overview](https://platform.claude.com/docs/en/models/overview), [DeepSeek model IDs](https://api-docs.deepseek.com/quick_start/pricing/), [MiMo models](https://mimo.mi.com/docs/en-US/quick-start/model).

`python tools/refresh_model_catalogs.py` explicitly refreshes the configured account catalogs without importing the bot or making generation requests. It preserves previous caches on failure and writes a local report under ignored `model_catalogs_cache/`. Text-route counts exclude dedicated image, audio, embedding and other specialized models; they do not claim all routes are usable. Neon is retained as a research / organizer candidate. Existing chat, vision and named-model features remain. The narrowly scoped paid Gemini backup policy is unchanged.
