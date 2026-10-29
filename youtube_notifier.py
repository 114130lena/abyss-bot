"""Free YouTube channel upload notifications using the official Atom feed.

Add channel IDs to youtube_channels.txt or YOUTUBE_CHANNEL_IDS in .env to enable.
"""
import json
import threading
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from dotenv import load_dotenv
from resilience import send_webhook, warn_failure
from banner_manager import get_banner
from discord_v2 import payload as v2_payload
from creator_groups import youtube_group, role_for

BASE_DIR = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
load_dotenv(BASE_DIR / ".env")
CHANNELS_FILE = BASE_DIR / "youtube_channels.txt"
HANDLE_CACHE_FILE = BASE_DIR / "youtube_handle_ids.json"
_handle_retry_at = {}
_CHANNEL_ID_PATTERN = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_CHANNEL_ID_EXTRACT = re.compile(r'"(?:channelId|externalId)"\s*:\s*"(UC[A-Za-z0-9_-]{22})"')


def _resolve_handle(handle):
    """Resolve and cache a YouTube @handle; never invent a channel ID."""
    cache = _load_handle_cache()
    if handle in cache and _CHANNEL_ID_PATTERN.fullmatch(cache[handle]):
        return cache[handle]
    now = time.monotonic()
    if now < _handle_retry_at.get(handle, 0):
        return None
    _handle_retry_at[handle] = now + 3600
    try:
        response = requests.get("https://www.youtube.com/" + handle, timeout=12,
                                headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        if "/@" not in response.url:
            raise ValueError("YouTube did not return a channel handle page")
        candidates = _CHANNEL_ID_EXTRACT.findall(response.text)
        if not candidates:
            raise ValueError("channelId metadata missing in channel page")
        channel_id = candidates[0]
        feed = requests.get("https://www.youtube.com/feeds/videos.xml",
                            params={"channel_id": channel_id}, timeout=12)
        feed.raise_for_status()
        root = ET.fromstring(feed.content)
        if root.tag != "{http://www.w3.org/2005/Atom}feed":
            raise ValueError("Resolved channel feed format invalid")
        # The feed check prevents caching nonexistent IDs.
        cache[handle] = channel_id
        _save_handle_cache(cache)
        logging.info("YouTube handle resolved: %s => %s", handle, channel_id)
        return channel_id
    except (requests.RequestException, ValueError, ET.ParseError) as exc:
        logging.warning("YouTube handle resolution pending: %s: %s", handle, exc)
        return None


def _load_handle_cache():
    try:
        value = json.loads(HANDLE_CACHE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _save_handle_cache(value):
    HANDLE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = HANDLE_CACHE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(HANDLE_CACHE_FILE)


def load_channel_ids():
    """Merge environment IDs and channel file; resolve @handles safely."""
    candidates = [v.strip() for v in os.getenv("YOUTUBE_CHANNEL_IDS", "").split(",") if v.strip()]
    try:
        candidates.extend(line.split("#", 1)[0].strip() for line in CHANNELS_FILE.read_text(encoding="utf-8").splitlines())
    except FileNotFoundError:
        pass
    result = []
    for value in candidates:
        if _CHANNEL_ID_PATTERN.fullmatch(value):
            channel_id = value
        elif re.fullmatch(r"@[A-Za-z0-9_.-]{3,40}", value):
            channel_id = _resolve_handle(value)
        else:
            continue
        if channel_id and channel_id not in result:
            result.append(channel_id)
    return result


INTERVAL = max(60, int(os.getenv("YOUTUBE_POLL_INTERVAL", "60")))
WEBHOOK_URL = os.getenv("YOUTUBE_DISCORD_WEBHOOK_URL", os.getenv("DISCORD_WEBHOOK_URL", "")).strip()
SEEN_FILE = BASE_DIR / "youtube_seen_videos.json"
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
last_check = 0.0
_state_lock = threading.RLock()
failures = 0
_channel_failures = {}
_channel_retry_at = {}
session = requests.Session()
session.headers.update({"User-Agent": "abyss-bot/1.0"})


def _load_seen():
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def _save_seen(seen):
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = SEEN_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(SEEN_FILE)


def _fetch_uploads(channel_id):
    res = session.get("https://www.youtube.com/feeds/videos.xml", params={"channel_id": channel_id}, timeout=15)
    res.raise_for_status()
    root = ET.fromstring(res.content)
    if root.tag != ATOM + "feed":
        raise ValueError("Unexpected YouTube feed format")
    actual_id = (root.findtext(YT + "channelId") or "").strip()
    if actual_id and actual_id != channel_id and "UC" + actual_id != channel_id:
        raise ValueError(f"YouTube feed channel mismatch: requested={channel_id}, returned={actual_id}")
    channel_name = root.findtext(ATOM + "title") or channel_id
    videos = []
    for entry in root.findall(ATOM + "entry"):
        video_id = (entry.findtext(YT + "videoId") or "").strip()
        if not video_id:
            continue
        videos.append({
            "id": video_id,
            "title": entry.findtext(ATOM + "title") or "새 영상",
            "url": "https://www.youtube.com/watch?v=" + video_id,
            "published": entry.findtext(ATOM + "published") or "",
            "channel": channel_name,
            "channel_id": channel_id,
        })
    return list(reversed(videos))


def _youtube_banner(channel_id):
    replay_id = _load_handle_cache().get("@Hiyorireplay", "")
    group = youtube_group(channel_id, replay_id)
    return get_banner(group) if group else ""


def _notify(video):
    channel_id = video.get("channel_id", "")
    replay_channel_id = _load_handle_cache().get("@Hiyorireplay", "")
    group = youtube_group(channel_id, replay_channel_id)
    role_id = role_for(group) if group else ""
    banner = _youtube_banner(channel_id)
    payload = v2_payload("youtube", role_id, banner,
                         "새로운 유튜브 영상이 업로드되었습니다!",
                         video["channel"], "**영상 제목**\n" + video["title"][:500],
                         media_url="https://i.ytimg.com/vi/" + video["id"] + "/hqdefault.jpg",
                         media_description="유튜브 영상 썸네일",
                         link_url=video["url"], link_label="영상 바로가기",
                         username="유튜브 업로드 알림")
    send_webhook(WEBHOOK_URL, payload)


def check_youtube():
    """Check configured channels; first sight of a channel does not alert for old uploads."""
    global last_check, failures
    channel_ids = load_channel_ids()
    if not channel_ids:
        return
    now = time.monotonic()
    if now - last_check < INTERVAL:
        return
    last_check = now
    if not WEBHOOK_URL.startswith("https://"):
        logging.warning("YouTube: no valid Discord webhook configured")
        return
    with _state_lock:
        _check_youtube_channels(channel_ids)


def _check_youtube_channels(channel_ids):
    """One broken feed must never block other creator notifications."""
    seen = _load_seen()
    for channel_id in channel_ids:
        if time.monotonic() < _channel_retry_at.get(channel_id, 0):
            continue
        try:
            videos = _fetch_uploads(channel_id)
            if channel_id not in seen:
                seen[channel_id] = [v["id"] for v in videos][-100:]
                _save_seen(seen)
                logging.info("YouTube %s: initialized with %d previous uploads", channel_id, len(videos))
                continue
            ids = set(seen[channel_id])
            for video in videos:
                if video["id"] in ids:
                    continue
                _notify(video)
                seen[channel_id] = (seen[channel_id] + [video["id"]])[-100:]
                ids.add(video["id"])
                _save_seen(seen)
                logging.info("YouTube notification sent: %s", video["url"])
            _channel_failures[channel_id] = 0
            _channel_retry_at.pop(channel_id, None)
        except (requests.RequestException, ET.ParseError, ValueError, OSError) as exc:
            count = _channel_failures.get(channel_id, 0) + 1
            _channel_failures[channel_id] = count
            delay = min(1800, INTERVAL * (2 ** min(count, 4)))
            _channel_retry_at[channel_id] = time.monotonic() + delay
            warn_failure("YouTube " + channel_id, count, exc)
            continue


def receive_websub_feed(xml_bytes):
    """Apply signed WebSub feed updates using the same RSS deduplication ledger."""
    root = ET.fromstring(xml_bytes)
    if root.tag != ATOM + "feed":
        raise ValueError("Unexpected WebSub feed")
    entries = root.findall(ATOM + "entry")
    feed_channel = (root.findtext(YT + "channelId") or (entries[0].findtext(YT + "channelId") if entries else "") or "").strip()
    allowed_channels = set(load_channel_ids())
    if not feed_channel or feed_channel not in allowed_channels:
        raise ValueError("WebSub channel is not subscribed")
    with _state_lock:
        seen = _load_seen()
        if feed_channel not in seen:
            # Do not emit historical pushes before the RSS baseline is established.
            logging.warning("YouTube WebSub ignored before RSS initialization: %s", feed_channel)
            return
        known = set(seen[feed_channel])
        channel = root.findtext(ATOM + "title") or feed_channel
        for entry in entries:
            if (entry.findtext(YT + "channelId") or "").strip() != feed_channel:
                continue
            video_id = (entry.findtext(YT + "videoId") or "").strip()
            if not video_id or video_id in known:
                continue
            video = {
                "id": video_id, "channel_id": feed_channel,
                "channel": channel,
                "title": entry.findtext(ATOM + "title") or "새 영상",
                "published": entry.findtext(ATOM + "published") or "",
                "url": "https://www.youtube.com/watch?v=" + video_id,
            }
            _notify(video)
            seen[feed_channel] = (seen[feed_channel] + [video_id])[-100:]
            known.add(video_id)
            _save_seen(seen)
            logging.info("YouTube WebSub notification sent: %s", video["url"])
