"""Edit an existing Discord message without ever posting a replacement.

Bot-owned messages use the channel PATCH API. Webhook-owned messages require
a matching webhook token; reject credentials for a different webhook.
"""
import logging
import os
from urllib.parse import urlsplit
import requests


def _matching_webhook(channel, message_id, headers, get):
    if not any(os.getenv(name, "").strip() for name in (
            "SERVER_BANNER_INFO_WEBHOOK_URL", "CHZZK_CLIP_WEBHOOK_URL",
            "DISCORD_WEBHOOK_URL")):
        return None, "webhook_credential_missing"
    url = f"https://discord.com/api/v10/channels/{channel}/messages/{message_id}"
    try:
        response = get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            return None, "message_not_readable"
        data = response.json()
        if not isinstance(data, dict):
            return None, "invalid_message"
        webhook_id = str(data.get("webhook_id") or "")
        if not webhook_id.isdigit():
            return None, "non_webhook_author"
        candidates = ("SERVER_BANNER_INFO_WEBHOOK_URL", "CHZZK_CLIP_WEBHOOK_URL",
                      "DISCORD_WEBHOOK_URL")
        for name in candidates:
            configured = os.getenv(name, "").strip()
            parsed = urlsplit(configured)
            if parsed.scheme != "https" or parsed.hostname not in ("discord.com", "discordapp.com"):
                continue
            chunks = parsed.path.strip("/").split("/")
            if len(chunks) == 4 and chunks[0:2] == ["api", "webhooks"]:
                wid, token = chunks[2], chunks[3]
            elif len(chunks) == 5 and chunks[0:3] == ["api", "v10", "webhooks"]:
                wid, token = chunks[3], chunks[4]
            else:
                continue
            if wid == webhook_id and token:
                return f"https://discord.com/api/v10/webhooks/{wid}/{token}/messages/{message_id}", "matching_webhook"
        return None, "webhook_credential_missing"
    except (requests.RequestException, ValueError, TypeError):
        return None, "message_lookup_failed"


def update_original(channel, message_id, payload, bot_token, *,
                    get=requests.get, patch=requests.patch):
    """Return status only, never credentials; do not create Discord messages."""
    if not channel.isdigit() or not message_id.isdigit() or not bot_token:
        return "not_configured"
    url = f"https://discord.com/api/v10/channels/{channel}/messages/{message_id}"
    headers = {"Authorization": "Bot " + bot_token}
    result = patch(url, headers=headers, json=payload, timeout=12)
    if result.status_code not in (403, 404):
        result.raise_for_status()
        return "updated"
    webhook_url, reason = _matching_webhook(message_id=message_id, channel=channel,
                                           headers=headers, get=get)
    if not webhook_url:
        logging.warning("DISCORD_ORIGINAL_EDIT_BLOCKED message=%s reason=%s http=%s",
                        message_id, reason, result.status_code)
        return reason
    result = patch(webhook_url, json=payload, timeout=12)
    if result.status_code in (403, 404):
        logging.warning("DISCORD_ORIGINAL_WEBHOOK_EDIT_BLOCKED message=%s http=%s",
                        message_id, result.status_code)
        return "webhook_uneditable"
    result.raise_for_status()
    return "updated"
