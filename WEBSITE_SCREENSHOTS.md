# Public website screenshots

This optional integration opens fixed public routes at `https://frostmoonrui.com` in a fresh, sandboxed Chromium context. It runs only after an explicit mention in a group, or an explicit private-chat command. It never signs in to the owner studio, reuses a personal browser profile, exposes the bot host, or sends scheduled announcements.

Install into the same Python environment used by the bot:

```powershell
python -m pip install -r requirements-screenshots.txt
python -m playwright install chromium
```

An existing compatible Chromium executable may instead be selected through the local-only `WEBSITE_BROWSER_PATH` setting. Never point this setting at a browser profile directory. Screenshot browser support is optional: missing support produces a terminal diagnostic and a short user-visible failure, not an old screenshot.

The command accepts a fixed page identifier, language and theme:

```text
website screenshot:home zh dark
website screenshot:friends en light
website screenshot:resources ja dark
```

Public page identifiers: `home`, `about`, `friends`, `resources`, `food`, `projects`. Languages: `zh`, `en`, `ru`, `ja`. Themes: `light`, `dark`. Routes not yet deployed correctly fail instead of capturing a 404 page. Localized runtime adapters may provide additional aliases without altering these public identifiers.

Only one capture runs at a time. Repeated requests from the same user are limited to one every 30 seconds. No arbitrary URL, private address, owner endpoint, navigation redirect, download, POST or external tracking request is allowed. Necessary Amap subresources are restricted to the existing provider domains. Page content changes are reflected at the next explicit capture; no periodic synchronization or screenshot cache is used. Output is a bounded PNG sent as a OneBot `base64://` image segment to the requesting chat, never added to the sticker collection.

Install the optional dependencies yourself before using the feature. Offline tests mock browser behavior; they do not start the bot, open websites or send QQ messages.
