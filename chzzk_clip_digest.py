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
    # This undocumented public endpoint occasionally stalls on large pages.
    # Bound retry cost and avoid blocking the other bot workers.
    fetch = get or requests.get
    last_error = None
    for size in (30, 10):
        try:
            response = fetch(url, params={
                "filterType": "ALL", "orderType": "RECENT",
                "page": 0, "size": size}, timeout=(2, 7),
                headers={"User-Agent": "Mozilla/5.0 (compatible; abyss-bot/1.0)",
                         "Accept": "application/json",
                         "Referer": "https://chzzk.naver.com/"})
            response.raise_for_status()
            root = response.json()
            break
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            logging.warning("CHZZK_CLIP_SOURCE_RETRY page_size=%d reason=%s",
                            size, type(exc).__name__)
    else:
        logging.warning("CHZZK_CLIP_SOURCE_UNAVAILABLE after_two_attempts")
        raise last_error
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
        if not timedelta(0) <= age <= timedelta(days=30):
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
    # One page may contain fewer than ten eligible clips. Read additional
    # pages only as needed, without increasing the first request timeout.
    if len(accepted) < 10:
        seen = {clip["id"] for clip in accepted}
        for page in (1, 2):
            try:
                extra = fetch(url, params={
                    "filterType": "ALL", "orderType": "RECENT",
                    "page": page, "size": size}, timeout=(2, 7),
                    headers={"Accept": "application/json",
                             "Referer": "https://chzzk.naver.com/"})
                extra.raise_for_status()
                more = extra.json()
                batch = (more.get("content") or {}).get("data") if isinstance(more, dict) else None
                if not isinstance(batch, list) or not batch:
                    break
                for item in batch:
                    if not isinstance(item, dict) or item.get("ownerChannelId") != CHANNEL_ID:
                        continue
                    uid = str(item.get("clipUID", ""))
                    dt = _date(item.get("createdDate"))
                    if not _ID.fullmatch(uid) or uid in seen or not dt:
                        continue
                    age = now - dt.astimezone(KST)
                    if not timedelta(0) <= age <= timedelta(days=30):
                        continue
                    if (item.get("adult") or item.get("privateUserBlock")
                            or item.get("blindType") not in (None, "", "NONE", "NORMAL", "PUBLIC")):
                        continue
                    image = str(item.get("thumbnailImageUrl") or "")
                    parsed = urlsplit(image)
                    if parsed.scheme != "https" or not parsed.hostname:
                        image = ""
                    accepted.append({"id": uid,
                                     "title": str(item.get("clipTitle") or "히요리 클립")[:160],
                                     "url": "https://chzzk.naver.com/clips/" + uid,
                                     "thumbnail": image,
                                     "views": max(0, int(item.get("readCount") or 0)),
                                     "date": dt.astimezone(KST).isoformat()})
                    seen.add(uid)
                if len(accepted) >= 10:
                    break
            except (requests.RequestException, ValueError, TypeError):
                logging.info("CHZZK_CLIP_EXTRA_PAGE_UNAVAILABLE page=%d", page)
                break
    # Prioritize clips from the last 24 hours, then fill the remaining slots
    # with older clips (up to 30 days), without repeating a clip.
    unique = {clip["id"]: clip for clip in accepted}
    return sorted(unique.values(), key=lambda c: (
        0 if now - datetime.fromisoformat(c["date"]) <= timedelta(hours=24) else 1,
        -c["views"], c["id"]))[:10]


def _render(clips, history):
    if not clips:
        return None
    lead = clips[0]
    parts = [
        {"type": 10, "content": "**HANAYO HIYORI** · STELLIVE · ABYSS"},
        {"type": 14, "divider": True, "spacing": 1},
        {"type": 10, "content": "**오늘의 히요리 클립**\n"
         "최근 24시간 우선 · 이전 클립 포함 " + str(len(clips[:10])) + "개"},
    ]
    # A Discord gallery can display up to ten images in a single message.
    # CHZZK's public API exposes thumbnails, not reliable direct MP4 files;
    # preserve clickable source links instead of falsely claiming playback.
    media = [{"media": {"url": clip["thumbnail"]},
              "description": clip["title"][:120]}
             for clip in clips[:10] if clip.get("thumbnail")]
    if media:
        parts.append({"type": 12, "items": media[:10]})
    links = []
    for index, clip in enumerate(clips[:10], 1):
        title = clip["title"].replace("[", "").replace("]", "").replace("\n", " ")
        links.append(str(index) + ". [" + title[:75] + "](" + clip["url"] + ")")
    parts.append({"type": 10, "content": "\n".join(links)[:3500]})
    previous = []
    for day, clip in sorted(history.items(), reverse=True)[:7]:
        if isinstance(clip, dict) and _ID.fullmatch(str(clip.get("id", ""))):
            previous.append("[" + day + "](https://chzzk.naver.com/clips/" + clip["id"] + ")")
    if previous:
        parts.append({"type": 14, "divider": True, "spacing": 1})
        parts.append({"type": 10, "content": "**지난 대표 클립** · " + " · ".join(previous)})
    return {"components": [{"type": 17, "accent_color": 0x8391BB,
                             "components": parts}],
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
    from discord_message_sync import update_original
    status = update_original(channel, message, payload, token,
                             get=get or requests.get, patch=patch or requests.patch)
    if status != "updated":
        logging.warning("CHZZK_CLIP_ORIGINAL_UNEDITABLE message=%s reason=%s; no replacement sent",
                        message, status)
        return {"status": "original_message_uneditable", "reason": status}
    save_json(STATE_FILE, {"history": updated,
                           "latest_id": clips[0]["id"],
                           "last_updated": now.isoformat()})
    logging.info("CHZZK_CLIP_UPDATED id=%s available=%d", clips[0]["id"], len(clips))
    return {"status": "updated", "clip": clips[0]["id"], "count": len(clips)}


def run_clip_scheduler():
    """KST: update at 18:00 and every 2 hours until 22:00, not overnight."""
    last_slot = None
    # Update the original immediately on startup, including after deploy.
    # Time slots remain 18/20/22 KST, with no overnight polling.
    first = True
    while True:
        now = datetime.now(KST)
        slot = now.strftime("%Y-%m-%d %H")
        due = now.hour in (18, 20, 22) and slot != last_slot
        if first or due:
            first = False
            if due:
                last_slot = slot
            try:
                logging.info("CHZZK_CLIP_RESULT %s", refresh_once(now))
            except (requests.RequestException, OSError, ValueError, TypeError):
                logging.exception("클립 업데이트 실패: 기존 메시지 유지")
        time.sleep(30)
