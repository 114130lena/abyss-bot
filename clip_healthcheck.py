"""Read-only scheduled verification of the original clip Discord message.

Clip data is collected every two hours, but ONLY the existing message is
published at 08:00 and 18:00 KST. This health check never edits/posts clips.
"""
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from resilience import load_json

KST = ZoneInfo("Asia/Seoul")


def expected_slot(now=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    if now.hour >= 18:
        hour, day = 18, now
    elif now.hour >= 8:
        hour, day = 8, now
    else:
        hour, day = 18, now - timedelta(days=1)
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


def check(now=None, get=None):
    load_dotenv(Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot")) / ".env")
    from chzzk_clip_digest import STATE_FILE
    now = (now or datetime.now(KST)).astimezone(KST)
    expected = expected_slot(now)
    target_slot = expected.strftime("%Y-%m-%d %H")
    state = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    clips = state.get("clips") if isinstance(state.get("clips"), list) else []
    actual_slot = str(state.get("last_published_slot") or "")
    result = {"expected_slot": target_slot, "actual_slot": actual_slot,
              "cached_clips": len(clips)}
    if actual_slot != target_slot:
        return {**result, "status": "missing_published_slot"}
    try:
        published = datetime.fromisoformat(str(state.get("last_updated") or ""))
        if published.tzinfo is None or published.astimezone(KST) < expected:
            return {**result, "status": "stale_publication_time"}
    except (ValueError, TypeError):
        return {**result, "status": "invalid_publication_time"}
    channel = os.getenv("CHZZK_CLIP_CHANNEL_ID", "").strip()
    message = os.getenv("CHZZK_CLIP_MESSAGE_ID", "").strip()
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not channel.isdigit() or not message.isdigit() or not token:
        return {**result, "status": "missing_discord_credentials"}
    fetch = get or requests.get
    response = fetch(f"https://discord.com/api/v10/channels/{channel}/messages/{message}",
                     headers={"Authorization": "Bot " + token}, timeout=12)
    response.raise_for_status()
    posted = response.json()
    if not isinstance(posted, dict) or str(posted.get("id")) != message:
        return {**result, "status": "original_missing"}
    if not (int(posted.get("flags") or 0) & 32768) or not posted.get("components"):
        return {**result, "status": "original_invalid_components"}
    try:
        edit = datetime.fromisoformat(str(posted.get("edited_timestamp") or "").replace("Z", "+00:00"))
        if edit.tzinfo is None or edit.astimezone(KST) < expected:
            return {**result, "status": "original_not_edited_this_slot"}
    except (ValueError, TypeError):
        return {**result, "status": "missing_edited_timestamp"}
    return {**result, "status": "ok"}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        result = check()
    except (OSError, ValueError, TypeError, requests.RequestException) as exc:
        logging.warning("CHZZK_CLIP_HEALTH_FAILED reason=%s", type(exc).__name__)
        result = {"status": "unavailable"}
    print("CHZZK_CLIP_HEALTH " + json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "ok" else 1)
