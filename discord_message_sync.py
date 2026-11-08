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



def _components_v2_payload(payload):
    """Convert a simple existing-message update into Components V2.

    A message already marked IS_COMPONENTS_V2 cannot be edited with embeds
    or content. Preserve its original message ID and the information supplied.
    """
    parts = []
    text = str(payload.get("content") or "").strip()
    if text:
        parts.append({"type": 10, "content": text[:2000]})
    for embed in payload.get("embeds") or []:
        if not isinstance(embed, dict):
            continue
        caption = []
        if embed.get("title"):
            caption.append("## " + str(embed["title"])[:200])
        if embed.get("description"):
            caption.append(str(embed["description"])[:2000])
        if embed.get("footer") and isinstance(embed["footer"], dict):
            caption.append("-# " + str(embed["footer"].get("text") or "")[:300])
        if caption:
            parts.append({"type": 10, "content": "\\n\\n".join(caption)[:3500]})
        image = (embed.get("image") or {}).get("url") if isinstance(embed.get("image"), dict) else ""
        if isinstance(image, str) and image.startswith("https://"):
            parts.append({"type": 12, "items": [{"media": {"url": image}}]})
    # Nonempty components, keep at most 35 components per card.
    if not parts:
        raise ValueError("No display content for existing Components V2 message")
    return {"components": [{"type": 17, "components": parts[:30]}],
            "allowed_mentions": {"parse": []}}


def _safe_discord_error(result, message_id):
    try:
        error = result.json()
        if isinstance(error, dict):
            code = error.get("code", "unknown")
            # Numeric code only; Discord free-text messages may echo URLs.
            logging.warning("DISCORD_ORIGINAL_REJECTED message=%s http=%s code=%s",
                            message_id, result.status_code,
                            code if isinstance(code, int) else "unknown")
    except (ValueError, TypeError):
        pass


def update_original(channel, message_id, payload, bot_token, *,
                    get=requests.get, patch=requests.patch):
    """Return status only, never credentials; do not create Discord messages."""
    if not channel.isdigit() or not message_id.isdigit() or not bot_token:
        return "not_configured"
    url = f"https://discord.com/api/v10/channels/{channel}/messages/{message_id}"
    headers = {"Authorization": "Bot " + bot_token}
    result = patch(url, headers=headers, json=payload, timeout=12)
    if result.status_code in (200, 204):
        return "updated"
    if result.status_code == 400:
        # Most existing user-created information cards are Components V2.
        # Read its flags before trying a second format; never send a new post.
        try:
            original = get(url, headers=headers, timeout=10)
            if original.status_code == 200 and isinstance(original.json(), dict):
                details = original.json()
                if int(details.get("flags") or 0) & 32768:
                    v2 = _components_v2_payload(payload)
                    result = patch(url, headers=headers, json=v2, timeout=12)
                    if result.status_code in (200, 204):
                        logging.info("DISCORD_ORIGINAL_V2_UPDATED message=%s", message_id)
                        return "updated"
        except (requests.RequestException, ValueError, TypeError):
            logging.warning("DISCORD_ORIGINAL_FORMAT_CHECK_FAILED message=%s",message_id)
    if result.status_code not in (403, 404):
        _safe_discord_error(result, message_id)
        return "discord_rejected_" + str(result.status_code)
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
