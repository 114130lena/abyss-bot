"""Refresh one existing Discord clip message without creating new messages.

Opt-in: CHZZK_CLIP_CHANNEL_ID and CHZZK_CLIP_MESSAGE_ID must both exist.
The public CHZZK channel clip endpoint may change; failure retains the
previous Discord message and state.
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from creator_groups import CHZZK_GROUPS
from resilience import load_json, save_json

KST = ZoneInfo("Asia/Seoul")
CHANNEL_ID = next(key for key, value in CHZZK_GROUPS.items() if value == "hanayo")
STATE_FILE = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot")) / "daily_clip_state.json"
_ID = re.compile(r"^[0-9A-Za-z_-]{6,128}$")


def _date(value):
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value / (1000 if value > 1e11 else 1), timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.replace(tzinfo=KST) if result.tzinfo is None else result
    except ValueError:
        return None


def fetch_recent_clips(now=None, get=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    url = f"https://api.chzzk.naver.com/service/v1/channels/{CHANNEL_ID}/clips"
    response = (get or requests.get)(url, params={
        "clipUID": "", "filterType": "ALL", "orderType": "RECENT",
        "page": 0, "size": 100}, timeout=(3, 12))
    response.raise_for_status()
    root = response.json()
    if not isinstance(root, dict) or root.get("code") != 200:
        raise ValueError("Invalid CHZZK clip response")
    block = root.get("content") or {}
    items = block.get("data")
    if not isinstance(items, list):
        raise ValueError("Clip list missing")
    accepted = []
    for item in items:
        if not isinstance(item, dict) or item.get("ownerChannelId") != CHANNEL_ID:
            continue
        uid = str(item.get("clipUID", ""))
        dt = _date(item.get("createdDate"))
        if not _ID.fullmatch(uid) or not dt:
            continue
        age = now - dt.astimezone(KST)
        if not timedelta(0) <= age <= timedelta(hours=24):
            continue
        if (item.get("adult") or item.get("privateUserBlock")
                or item.get("blindType") not in (None, "", "NONE", "NORMAL", "PUBLIC")):
            continue
        image = str(item.get("thumbnailImageUrl") or "")
        parsed = urlsplit(image)
        if parsed.scheme != "https" or not parsed.hostname:
            image = ""
        views = int(item.get("readCount") or 0)
        accepted.append({"id": uid, "title": str(item.get("clipTitle") or "히요리 클립")[:160],
                         "url": "https://chzzk.naver.com/clips/" + uid,
                         "thumbnail": image, "views": max(0, views),
                         "date": dt.astimezone(KST).isoformat()})
    return sorted(accepted, key=lambda c: (-c["views"], c["id"]))


def _render(clips, history):
    if not clips:
        return None
    lead = clips[0]
    lines = ["**오늘의 히요리 클립**", "",
             "**[" + lead["title"] + "](" + lead["url"] + ")**",
             "", "**최근 24시간 클립**"]
    for item in clips[:10]:
        title = item["title"].replace("[", "").replace("]", "").replace("\n", " ")
        lines.append("• [" + title[:95] + "](" + item["url"] + ")")
    previous = []
    for day, clip in sorted(history.items(), reverse=True)[:7]:
        if isinstance(clip, dict) and _ID.fullmatch(str(clip.get("id", ""))):
            previous.append("[" + day + "](https://chzzk.naver.com/clips/" + clip["id"] + ")")
    if previous:
        lines.append("")
        lines.append("**지난 대표 클립** · " + " · ".join(previous))
    content = "\n".join(lines)
    embed = {"title": "하나요 히요리 · 클립", "url": lead["url"],
             "description": "클립을 누르면 치지직에서 재생됩니다.",
             "color": 0x8EA1DF}
    if lead["thumbnail"]:
        embed["image"] = {"url": lead["thumbnail"]}
    return {"content": content[:2000], "embeds": [embed],
            "allowed_mentions": {"parse": []}}


def refresh_once(now=None, get=None, patch=None):
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    channel = os.getenv("CHZZK_CLIP_CHANNEL_ID", "").strip()
    message = os.getenv("CHZZK_CLIP_MESSAGE_ID", "").strip()
    if not token or not channel.isdigit() or not message.isdigit():
        return {"status": "not_configured"}
    now = (now or datetime.now(KST)).astimezone(KST)
    clips = fetch_recent_clips(now, get=get)
    if not clips:
        return {"status": "no_recent_clips"}
    state = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    history = state.get("history") if isinstance(state.get("history"), dict) else {}
    day = now.strftime("%Y-%m-%d")
    updated = dict(history)
    updated[day] = {"id": clips[0]["id"], "title": clips[0]["title"]}
    updated = dict(sorted(updated.items(), reverse=True)[:14])
    payload = _render(clips, {k: v for k, v in updated.items() if k != day})
    if not payload:
        return {"status": "no_recent_clips"}
    response = (patch or requests.patch)(
        f"https://discord.com/api/v10/channels/{channel}/messages/{message}",
        headers={"Authorization": "Bot " + token},
        json=payload, timeout=12)
    if response.status_code in (403, 404):
        logging.warning("CHZZK_CLIP_ORIGINAL_UNEDITABLE status=%s (no replacement sent)",
                        response.status_code)
        return {"status": "original_message_uneditable"}
    response.raise_for_status()
    save_json(STATE_FILE, {"history": updated,
                           "latest_id": clips[0]["id"],
                           "last_updated": now.isoformat()})
    logging.info("CHZZK_CLIP_UPDATED id=%s available=%d", clips[0]["id"], len(clips))
    return {"status": "updated", "clip": clips[0]["id"], "count": len(clips)}


def run_clip_scheduler():
    """KST: update at 18:00 and every 2 hours until 22:00, not overnight."""
    last_slot = None
    while True:
        now = datetime.now(KST)
        slot = now.strftime("%Y-%m-%d %H")
        if now.hour in (18, 20, 22) and slot != last_slot:
            last_slot = slot
            try:
                logging.info("CHZZK_CLIP_RESULT %s", refresh_once(now))
            except (requests.RequestException, OSError, ValueError, TypeError):
                logging.exception("클립 업데이트 실패: 기존 메시지 유지")
        time.sleep(30)
