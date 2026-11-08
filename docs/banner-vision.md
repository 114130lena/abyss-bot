# Hiyori server-banner visual scoring (opt-in)

This project can evaluate cached hashtag artwork with an image-capable API. The current Oracle instance has about 1 GB RAM; **the vision model runs remotely, not on Oracle**.

## Activation

The classifier is **not active without an API key**. Do not commit a key to GitHub, paste one into Discord, or send it in a chat.

In `/home/ubuntu/chzzk-bot/.env` on Oracle, securely set:

```dotenv
OPENAI_API_KEY=YOUR_PRIVATE_OPENAI_API_KEY
SERVER_BANNER_VISION_ENABLED=1
SERVER_BANNER_VISION_MODEL=gpt-4.1-mini
```

Ensure the file is private (`chmod 600 .env`). Restart `chzzk-bot.service` to activate the background scoring worker; the independent guild-banner timer reads the same file. The existing server-banner authentication file remains untouched.

**API calls may incur usage charges**. No API calls happen until the opt-in flag and key are set. Each scan evaluates at most five previously unscored images, then caches results in `creator_media/fanart_vision_scores.json` keyed by image SHA-256 + model + official portrait reference. The worker starts on bot launch and refreshes every 30 minutes.

## What it detects

For each suitable wide image, the model sees **both** the candidate and Hiyori's official YouTube profile portrait. It returns JSON indicators of:

- whether a drawn character appears
- whether Hiyori appears based on the official reference
- how prominent Hiyori is
- how well the composition works as a 16:9 banner
- identification confidence

The score is weighted: prominence 45%, composition 35%, confidence 20%. The banner is eligible only when Hiyori appears with reasonable prominence, composition and confidence. Multiple creator hashtags and repost status are no longer blanket reasons for exclusion when vision is active.

The classifier is probabilistic and can make mistakes, especially with fan-art style variations, face-obscuring poses, multiple characters or reference-portrait ambiguity. Test on a sample pool and monitor the outcome before relying on it.

## Safe failure behavior

- With vision disabled, the previous metadata rules remain in place.
- With vision enabled but without a key, matching portrait or completed score, the image **is not automatically selected for guild banners**.
- Image evaluation does **not** delay Discord post notifications. Notification fanart stays independent.
- Existing guild banner remains unchanged when no scored candidates are available; old history and source credits are preserved.
- Strict source URL/prohibition checks, image integrity checks and attribution remain.

## Local sanity checks

```bash
cd /home/ubuntu/chzzk-bot
.venv/bin/python -c 'import fanart_vision as v; print("enabled",v.enabled(),"ready",v.ready())'
sudo journalctl -u chzzk-bot.service --since '1 hour ago' --no-pager | grep 'BANNER_VISION'
```

Never print the key or the contents of the environment file.
