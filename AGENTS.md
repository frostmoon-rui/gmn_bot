# Project collaboration

- Maintain English public source, comments, documentation, commands, prompts, replies and website UI. Preserve exact upstream protocol values and legacy private-data schemas through explicit compatibility adapters; do not merely encode untranslated user-facing text. Never rewrite stored personal records just to change the interface language.

- Use `CacheTide` as the contributor's public name in all tracked code, documentation, website text, tests, and source metadata. Do not add personal nicknames or private account identifiers to public files. Local private profiles are not part of this naming migration.

- After each requested project update, run appropriate checks, commit the scoped changes, and push to `origin` (`https://github.com/frostmoon-rui/gmn_bot`). The user explicitly requests GitHub synchronization after every update.
- Verify push success and report its commit. If network or authentication prevents synchronization, keep local work and clearly report that GitHub is not yet updated. Never claim success without verification; never force-push or overwrite remote changes.
- Never commit real API keys, `local_settings.json`, QQ/contact lists, personal profiles, chat records, location bindings, sticker libraries, SQLite databases, runtime caches, backups, or unrelated personal documents. Preserve these local files. Check staged contents before pushing.
- Preserve unrelated user edits; stage only the files belonging to the requested update.
- Do not start the live bot or run message-sending/network probes for offline verification. Use `python -m unittest discover -s tests -q` and proportionate static checks. Main-module import can initialize live state.
- Source synchronization does not authorize public website deployment, firewall/router changes, or QQ announcements.
