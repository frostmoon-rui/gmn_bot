# Hosting the map on your computer

The map can run alongside the bot without buying a cloud server. These instructions describe optional configuration; they do not themselves change listening addresses, Windows Firewall, your router, or public tunnels.

## 1. On the host computer

Start the bot and open `http://127.0.0.1:8788`. The address `127.0.0.1` always means the visitor's own computer, so it cannot be shared as a remote link.

## 2. On the same router or Wi-Fi

Use `ipconfig` to find the host's IPv4 address. The examples below use the illustrative address `192.168.1.100`; replace it with your actual LAN address. DHCP addresses can change; a router reservation can make the address stable.

In the same PowerShell window used to start the bot, set the binding and restart:

~~~powershell
$env:FOOD_MAP_BIND = '0.0.0.0'
python gemini_bot.py
~~~

Or start just the map:

~~~powershell
python food_map_server.py --host 0.0.0.0 --port 8788
~~~

Do not start two instances on port 8788. `0.0.0.0` is a listening address, not a browser destination. Devices on the same LAN can open:

~~~text
http://192.168.1.100:8788
http://192.168.1.100:8788/food-map/index.json
~~~

If Windows Firewall blocks access, first confirm that this is a trusted Private network. An administrator can then choose to add a rule restricted to the Private profile and local subnet:

~~~powershell
New-NetFirewallRule -DisplayName 'Cat Food Map - trusted LAN only' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8788 -Profile Private -RemoteAddress LocalSubnet
~~~

Keep the firewall enabled. Guest Wi-Fi isolation, a VPN, or a different subnet may also prevent access; troubleshooting does not require opening access from the entire internet.

## 3. Remote visitors and a public source URL

Addresses such as `192.168.*` are private and cannot be reached directly from the internet. Router forwarding also depends on a public IP, ISP inbound restrictions, and CGNAT. Leaving the computer running does not create a public endpoint.

An HTTPS reverse tunnel is one option without a cloud server. [Cloudflare Quick Tunnel documentation](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/) describes temporary HTTPS access to a local HTTP service. After explicitly choosing public hosting and installing the official `cloudflared` client, the command takes this form:

~~~powershell
cloudflared tunnel --url http://127.0.0.1:8788
~~~

Local setup does not install or run this command. It publishes the map service through a temporary address that may change and is not guaranteed to remain available. Before publication, configure rate limits and check the Amap JS key's domain allowlist. Do not directly forward the development Python server to the internet or serve the whole bot directory as a static root.

For a public federation source, supply an address like:

~~~text
https://food-map.example.com/food-map/index.json
~~~

CacheTide's HTTPS blog cannot reliably fetch a local HTTP private address, due to reachability, mixed-content rules, and browser private-network restrictions. Alternatively, host only the exported public food JSON on your own HTTPS static hosting. There is no need to deploy the whole bot. Never upload keys, the SQLite moderation database, personal profiles, or weather location files.

## Federated food maps

The documented [CacheTide repository configuration](https://github.com/CacheTide/cachetide.github.io/blob/main/valaxy.config.ts) uses `valaxy-addon-food-map` sources with `id`, `name`, `url`, and `homepage`. JSON uses `schemaVersion: 1`, GCJ-02 coordinates, and `spots`.

Our compatible export is `/food-map/index.json`. It includes only approved local submissions, excludes QQ/private information, and does not send the 26 imported CacheTide venues back to their source. Imported reference venues remain visible on our site, while the export may legitimately contain an empty `spots` list until local submissions are added.

External sources in `food_map/sources.json` must be administrator-configured public HTTPS JSON endpoints. Background synchronization runs at startup, then every 24 hours by default, deduplicating by city, name, and coordinates. Failures do not block the local directory. No JavaScript from another repository needs to be installed or executed.
