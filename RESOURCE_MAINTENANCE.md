# Runtime resource maintenance

The maintenance task starts 60 seconds after startup and runs every 24 hours.
Only expired disposable memory entries are removed. Active recognition/search
tasks, conversation context, personal records and sticker originals are preserved.
Python garbage collection runs off the event loop; it is not a promise that the
OS immediately releases every cached allocation or reduces the working set.

Generated-file policies:

| Directory | Age retention | Size target |
| --- | --- | --- |
| `logs/startup` | 14 days | 64 MiB |
| `weather_diagnostics` | 3 days | 32 MiB |
| `screenshots` | 7 days | 64 MiB |

Files modified within the last 24 hours are protected even above a size target.
Only allowlisted log/diagnostic/image suffixes are eligible. Symlinks, junctions,
paths escaping the project and files changed during the scan are excluded. At
most 200 files are removed per pass. Backups, Git recovery copies, code, settings,
chat/profile/location data, databases and sticker libraries are not file-cleanup
targets. Large recovery copies therefore still require a separate review.

Inspect without importing or starting the bot:

```powershell
python tools/maintain_runtime.py
python tools/maintain_runtime.py --apply
```

Successful vision observations use last-access retention (180 days, 20,000 rows)
inside their own cache database. Old schemas migrate in place. SQLite can reuse
freed pages; full VACUUM is not run against the active bot. No other database is
modified by maintenance. Terminal summaries report cache expiry, files/bytes,
garbage collection and recognition reuse counts; no QQ notifications are sent.

NapCat's Rkey warning concerns image-link authorization lookup services. Repeated
lookup failures can disable those services and activate NapCat's image fallback;
this alone does not disable text replies. An old quoted image can still fail to
download. This update does not replace external Rkey endpoints or weaken TLS.
