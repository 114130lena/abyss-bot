# YouTube WebSub (primary) + official RSS (backup)

The bot already imports and starts `youtube_websub.start_websub()` from `main.py`. If no callback URL or secret is configured, it leaves RSS polling enabled.

## Required preparation (Oracle Cloud / DNS)
1. Create an A record, e.g. `youtube.example.com`, resolving to the Oracle VM's public IPv4 address.
2. Allow inbound TCP 80 and 443 in Oracle Cloud VCN/subnet security list or NSG. If Ubuntu UFW is enabled, allow both ports there as well.
3. Confirm no other reverse proxy already manages this hostname. DNS/HTTPS public reachability is required by the Google WebSub hub.
4. In the project repository, `scripts/setup_youtube_websub_https.sh` is an optional one-time setup helper. Once DNS and inbound ports are verified, copy that script to the VM and run `sudo bash setup_youtube_websub_https.sh youtube.example.com`. It will install Caddy if necessary, configure only `/youtube/websub`, create a random secret in `/home/ubuntu/chzzk-bot/.env`, and restart the service. It refuses to overwrite an existing non-generated Caddyfile or existing callback setting.
5. Confirm a valid public HTTPS certificate is issued. `curl -I https://youtube.example.com/youtube/websub` may respond 501 because GET needs the hub verification query; a TLS connection and HTTP response indicates routing reaches the server.
6. Inspect `sudo journalctl -u chzzk-bot -n 100 --no-pager | grep -Ei 'WebSub|YouTube'`; the bot initiates subscriptions for IDs in `youtube_channels.txt` plus configured channels. A `YouTube WebSub subscription requested` log indicates the hub accepted the request, **not** that subscription verification has completed.
7. Keep RSS polling enabled: it shares `youtube_seen_videos.json` with WebSub under a lock, so an upload delivered through either path does not notify twice.

## Security and verification
- WebSub HTTP listener binds only to `127.0.0.1:8765`; do **not** expose 8765 to the internet.
- The receiver verifies the hub HMAC signature before processing an update.
- Do not send your `.env`, webhook, or secret to anyone.
- DNS, Oracle firewall and issuance of a TLS certificate are infrastructure tasks and are not automated by a GitHub code merge.
- The repository will remain RSS-only until `YOUTUBE_WEBSUB_CALLBACK_URL` and `YOUTUBE_WEBSUB_SECRET` are set and a reachable HTTPS proxy exists.
