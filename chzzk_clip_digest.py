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
from schedule_display import schedule_label, format_saved_time

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
    # Rank across recent pages, not merely the first ten recent uploads.
    # RECENT is upload order, whereas selection is by readCount.
    seen = {clip["id"] for clip in accepted}
    for page in range(1, 6):
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
                if not timedelta(0) <= age <= timedelta(hours=24):
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
        except (requests.RequestException, ValueError, TypeError):
            logging.info("CHZZK_CLIP_EXTRA_PAGE_UNAVAILABLE page=%d", page)
            break
    # Only clips created in the last 24 hours; never backfill older videos.
    unique = {clip["id"]: clip for clip in accepted}
    return sorted(unique.values(), key=lambda c: (-c["views"], c["id"]))[:10]


def _find_mp4_urls(node):
    """Collect HTTPS MP4 representations from the CHZZK playback response."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("BaseURL", "path", "url", "videoUrl", "videoURL"):
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, str) and item.startswith("https://") and ".mp4" in urlsplit(item).path.lower():
                        found.append(item)
            if isinstance(value, (dict, list)):
                found.extend(_find_mp4_urls(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_find_mp4_urls(item))
    return found


def clip_video_url(clip, get=None):
    """Resolve a fresh, short-lived MP4 URL; never persist signed media URLs."""
    fetch = get or requests.get
    uid = clip["id"]
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://chzzk.naver.com/"}
    try:
        detail = fetch(f"https://api.chzzk.naver.com/service/v1/clips/{uid}/detail",
                       headers=headers, timeout=(2, 5))
        detail.raise_for_status()
        root = detail.json()
        info = root.get("content") or {}
        if root.get("code") != 200 or not isinstance(info, dict):
            return ""
        if info.get("adult") or info.get("vodStatus") in ("NONE", "EXPIRED"):
            return ""
        video_id = info.get("videoId")
        if not isinstance(video_id, str) or not re.fullmatch(r"[0-9A-Fa-f]{32,40}", video_id):
            return ""
        rec_id = info.get("recId") or json.dumps({
            "seedClipUID": uid, "fromType": "GLOBAL", "listType": "RECOMMEND"})
        response = fetch("https://api-videohub.naver.com/shortformhub/feeds/v9/card",
                         params={"seedType": "SPECIFIC", "serviceType": "CHZZK",
                                 "seedMediaId": video_id, "mediaType": "VOD",
                                 "panelType": "sdk_chzzk",
                                 "referer": f"https://chzzk.naver.com/clips/{uid}",
                                 "recType": "CHZZK", "recId": rec_id,
                                 "enableReverse": "false", "adAllowed": "Y",
                                 "deviceType": "html5_mo"},
                         headers=headers, timeout=(2, 6))
        response.raise_for_status()
        playback = response.json()
        content = ((playback.get("card") or {}).get("content") or {})
        vod = content.get("vod") or {}
        if not vod.get("playable"):
            return ""
        candidates = _find_mp4_urls(vod.get("playback") or {})
        return candidates[0] if candidates else ""
    except (requests.RequestException, ValueError, TypeError, KeyError):
        logging.info("CHZZK_CLIP_VIDEO_UNAVAILABLE clip=%s", uid)
        return ""


def _render(clips, history, now=None, collected_at=None):
    if not clips:
        return None
    lead = clips[0]
    parts = [
        {"type": 10, "content": "**HANAYO HIYORI** · STELLIVE · ABYSS"},
        {"type": 14, "divider": True, "spacing": 1},
        {"type": 10, "content": "**오늘의 히요리 클립**\n"
         "최근 24시간 클립 · 조회수 기준 " + str(len(clips[:10])) + "개"
         + "\n\n**클립 수집 주기** · 2시간마다 (짝수 시각, KST)"
         + "\n**마지막 수집 완료** · " + format_saved_time(collected_at)
         + "\n**마지막 메시지 갱신** · " + format_saved_time(now)
         + "\n\n" + schedule_label(now)},
    ]
    # A Discord gallery can display up to ten images in a single message.
    # CHZZK's public API exposes thumbnails, not reliable direct MP4 files;
    # preserve clickable source links instead of falsely claiming playback.
    media = [{"media": {"url": clip.get("video_url") or clip["thumbnail"]},
              "description": clip["title"][:120]}
             for clip in clips[:10] if clip.get("thumbnail")]
    if media:
        # Platform alerts use one prominent banner, not a ten-tile wall.
        parts.insert(0, {"type": 12, "items": media[:1]})
        if len(media) > 1:
            parts.append({"type": 10, "content": "**다른 클립 미리보기**"})
            parts.append({"type": 12, "items": media[1:10]})
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
    return {"flags": 32768, "components": [{"type": 17, "accent_color": 0x8391BB,
                             "components": parts}],
            "allowed_mentions": {"parse": []}}


def collect_once(now=None, get=None):
    """Update the offline top-ten snapshot; NEVER edit a Discord message."""
    now = (now or datetime.now(KST)).astimezone(KST)
    clips = fetch_recent_clips(now, get=get)
    state = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    # Keep publication history untouched by the two-hour collection job.
    state["clips"] = clips
    state["collected_at"] = now.isoformat()
    state["collected_slot"] = now.strftime("%Y-%m-%d %H") if now.hour % 2 == 0 else ""
    save_json(STATE_FILE, state)
    logging.info("CHZZK_CLIP_COLLECTED count=%d at=%s", len(clips), now.isoformat())
    return {"status": "collected", "count": len(clips)}


def clip_publish_due(now, last_slot):
    """Only 08:00 and 18:00 KST may edit the public Discord clip card."""
    now = now.astimezone(KST)
    slot = now.strftime("%Y-%m-%d %H")
    return now.hour in (8, 18) and slot != last_slot


def publish_once(now=None, get=None, patch=None):
    """Publish cached clips to the existing message, never POST a replacement."""
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    channel = os.getenv("CHZZK_CLIP_CHANNEL_ID", "").strip()
    message = os.getenv("CHZZK_CLIP_MESSAGE_ID", "").strip()
    if not token or not channel.isdigit() or not message.isdigit():
        return {"status": "not_configured"}
    now = (now or datetime.now(KST)).astimezone(KST)
    state = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    refreshed = _date(state.get("collected_at"))
    if (refreshed is None or not timedelta(0) <= now - refreshed.astimezone(KST) <= timedelta(hours=2, minutes=10)):
        # The collection worker might have failed; try once on the display slot.
        result = collect_once(now, get=get)
        if result["status"] != "collected":
            return result
        state = load_json(STATE_FILE, {})
    clips = [dict(clip) for clip in state.get("clips", []) if isinstance(clip, dict)
             and _date(clip.get("date")) is not None
             and timedelta(0) <= now - _date(clip["date"]).astimezone(KST) <= timedelta(hours=24)]
    if not clips:
        return {"status": "no_recent_clips"}
    history = state.get("history") if isinstance(state.get("history"), dict) else {}
    day = now.strftime("%Y-%m-%d")
    updated = dict(history)
    updated[day] = {"id": clips[0]["id"], "title": clips[0]["title"]}
    updated = dict(sorted(updated.items(), reverse=True)[:14])
    # Signed playback URLs are intentionally looked up only on the display slot.
    for clip in clips:
        clip["video_url"] = clip_video_url(clip, get=get)
    logging.info("CHZZK_CLIP_INLINE_VIDEO resolved=%d total=%d",
                 sum(bool(c["video_url"]) for c in clips), len(clips))
    payload = _render(clips, {k: v for k, v in updated.items() if k != day},
                      now=now, collected_at=state.get("collected_at"))
    from discord_message_sync import update_original
    status = update_original(channel, message, payload, token,
                             get=get or requests.get, patch=patch or requests.patch)
    if status.startswith("discord_rejected_") and any(c.get("video_url") for c in clips):
        for clip in clips:
            clip.pop("video_url", None)
        fallback = _render(clips, {k: v for k, v in updated.items() if k != day},
                      now=now, collected_at=state.get("collected_at"))
        status = update_original(channel, message, fallback, token,
                                 get=get or requests.get, patch=patch or requests.patch)
        logging.warning("CHZZK_CLIP_INLINE_VIDEO_FALLBACK status=%s", status)
    if status != "updated":
        logging.warning("CHZZK_CLIP_ORIGINAL_UNEDITABLE message=%s reason=%s; no replacement sent",
                        message, status)
        return {"status": "original_message_uneditable", "reason": status}
    state["history"] = updated
    state["latest_id"] = clips[0]["id"]
    state["last_updated"] = now.isoformat()
    if now.hour in (8, 18):
        state["last_published_slot"] = now.strftime("%Y-%m-%d %H")
    save_json(STATE_FILE, state)
    logging.info("CHZZK_CLIP_UPDATED id=%s available=%d", clips[0]["id"], len(clips))
    return {"status": "updated", "clip": clips[0]["id"], "count": len(clips)}


def refresh_once(now=None, get=None, patch=None):
    """Explicit/manual refresh compatibility; scheduler uses separate jobs."""
    if not (os.getenv("DISCORD_BOT_TOKEN", "").strip()
            and os.getenv("CHZZK_CLIP_CHANNEL_ID", "").strip().isdigit()
            and os.getenv("CHZZK_CLIP_MESSAGE_ID", "").strip().isdigit()):
        return {"status": "not_configured"}
    result = collect_once(now, get=get)
    if result["status"] != "collected":
        return result
    return publish_once(now, get=get, patch=patch)


def clip_refresh_due(now, last_slot):
    """Collect on even KST hours, independently of 08:00/18:00 publication."""
    now = now.astimezone(KST)
    slot = now.strftime("%Y-%m-%d %H")
    return now.hour % 2 == 0 and slot != last_slot


def run_clip_scheduler():
    """Collect every 2h, edit the original Discord message at 08 and 18 KST."""
    first = True
    next_publish_attempt = 0.0
    while True:
        now = datetime.now(KST)
        state = load_json(STATE_FILE, {})
        if not isinstance(state, dict):
            state = {}
        collected_slot = state.get("collected_slot")
        # Startup collection is offline; restarting outside 08/18 NEVER edits Discord.
        if first or clip_refresh_due(now, collected_slot):
            first = False
            try:
                logging.info("CHZZK_CLIP_COLLECT_RESULT %s", collect_once(now))
            except (requests.RequestException, OSError, ValueError, TypeError):
                logging.exception("클립 수집 실패: 기존 메시지 유지")
        if (clip_publish_due(now, state.get("last_published_slot"))
                and time.monotonic() >= next_publish_attempt):
            # A failed slot can retry, without spamming the Discord API every 30s.
            next_publish_attempt = time.monotonic() + 300
            try:
                logging.info("CHZZK_CLIP_RESULT %s", publish_once(now))
            except (requests.RequestException, OSError, ValueError, TypeError):
                logging.exception("클립 메시지 갱신 실패: 기존 메시지 유지")
        time.sleep(30)
