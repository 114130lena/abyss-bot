"""Read-only verification of the active clip or replacement dashboard message.

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


def check_dashboard(now, get=None):
    """Verify the replacement card without publishing or changing bot state."""
    from community_dashboard import STATE, MARKER
    result = {"mode": "community_dashboard"}
    channel = os.getenv("COMMUNITY_DASHBOARD_CHANNEL_ID", "").strip()
    message = os.getenv("COMMUNITY_DASHBOARD_MESSAGE_ID", "").strip()
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if (os.getenv("COMMUNITY_DASHBOARD_ENABLED") != "1" or not token
            or not channel.isdigit() or not message.isdigit()
            or channel != os.getenv("CHZZK_CLIP_CHANNEL_ID", "").strip()
            or message != os.getenv("CHZZK_CLIP_MESSAGE_ID", "").strip()):
        return {**result, "status": "dashboard_configuration_invalid"}
    state = load_json(STATE, {})
    if not isinstance(state, dict) or str(state.get("message_id")) != message:
        return {**result, "status": "dashboard_state_missing"}
    # Hourly forced refresh, plus 15 minutes for scheduling/network delays.
    def fresh(value):
        try:
            stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
            return stamp.tzinfo is not None and (
                -timedelta(minutes=5) <= now - stamp <= timedelta(minutes=75))
        except (ValueError, TypeError):
            return False
    if not fresh(state.get("updated_at")):
        return {**result, "status": "dashboard_state_stale"}
    response = (get or requests.get)(
        f"https://discord.com/api/v10/channels/{channel}/messages/{message}",
        headers={"Authorization": "Bot " + token}, timeout=12)
    response.raise_for_status()
    posted = response.json()
    if not isinstance(posted, dict) or str(posted.get("id")) != message:
        return {**result, "status": "dashboard_message_missing"}
    components = json.dumps(posted.get("components") or [], ensure_ascii=False)
    if (not int(posted.get("flags") or 0) & 32768
            or any(label not in components for label in
                   (MARKER, "방송 일정", "기념일", "활동 통계"))):
        return {**result, "status": "dashboard_components_invalid"}
    if not fresh(posted.get("edited_timestamp")):
        return {**result, "status": "dashboard_message_stale"}
    # Compare the actual Discord schedule sections with the local, parsed
    # notice state. Fresh timestamps alone cannot establish correctness.
    from community_dashboard import render, NAMES
    expected = render(now)["components"][0]["components"]
    def schedule_sections(parts):
        sections = {}
        for part in parts:
            if not isinstance(part, dict):
                continue
            content = part.get("content")
            if not isinstance(content, str):
                continue
            for name in NAMES.values():
                heading = "### " + name + " · 방송 일정"
                if content.startswith(heading):
                    sections[name] = content
        return sections
    actual = posted.get("components") or []
    actual_parts = []
    def collect(parts):
        for part in parts:
            if isinstance(part, dict):
                actual_parts.append(part)
                collect(part.get("components") or [])
    collect(actual)
    expected_sections = schedule_sections(expected)
    actual_sections = schedule_sections(actual_parts)
    if actual_sections != expected_sections:
        return {**result, "status": "dashboard_schedule_mismatch",
                "expected_sections": len(expected_sections),
                "matched_sections": sum(
                    actual_sections.get(k) == v for k, v in expected_sections.items())}
    return {**result, "status": "ok"}


def check(now=None, get=None):
    load_dotenv(Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot")) / ".env")
    now = (now or datetime.now(KST)).astimezone(KST)
    if os.getenv("CHZZK_CLIP_ENABLED") == "0":
        return check_dashboard(now, get)
    from chzzk_clip_digest import STATE_FILE
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
