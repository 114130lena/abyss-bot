"""Free YouTube channel upload notifications using the official Atom feed.

Add channel IDs to youtube_channels.txt or YOUTUBE_CHANNEL_IDS in .env to enable.
"""
import json
import threading
import logging
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from dotenv import load_dotenv
from resilience import send_webhook, warn_failure

BASE_DIR = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
load_dotenv(BASE_DIR / ".env")
CHANNELS_FILE = BASE_DIR / "youtube_channels.txt"


def load_channel_ids():
    """Merge the existing environment setting and managed channel list."""
    ids = [v.strip() for v in os.getenv("YOUTUBE_CHANNEL_IDS", "").split(",") if v.strip()]
    try:
        lines = CHANNELS_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        channel_id = line.split("#", 1)[0].strip()
        if channel_id and channel_id not in ids:
            ids.append(channel_id)
    return ids

INTERVAL = max(60, int(os.getenv("YOUTUBE_POLL_INTERVAL", "60")))
WEBHOOK_URL = os.getenv("YOUTUBE_DISCORD_WEBHOOK_URL", os.getenv("DISCORD_WEBHOOK_URL", "")).strip()
SEEN_FILE = BASE_DIR / "youtube_seen_videos.json"
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
last_check = 0.0
_state_lock = threading.RLock()
failures = 0
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
    if actual_id and actual_id != channel_id:
        raise ValueError("YouTube feed channel mismatch")
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


ROLE_BY_CHANNEL = {
    "UCHAr9pWGS30akL4MQCQT4uQ": os.getenv("YOUTUBE_HANAYO_ROLE_ID", "").strip(),
    "UC0YQnenKBCu5sGb7H61n6HA": os.getenv("YOUTUBE_SAKIHANE_ROLE_ID", "").strip(),
}


def _notify(video):
    role_id = ROLE_BY_CHANNEL.get(video.get("channel_id", ""), "")
    payload = {
        "content": f"<@&{role_id}>" if role_id else "",

        "username": "유튜브 업로드 알림",
        "embeds": [{
            "title": video["title"][:256],
            "url": video["url"],
            "description": video["channel"] + " 채널에 새 영상이 올라왔습니다.",
            "color": 0xFF0000,
            "thumbnail": {"url": "https://i.ytimg.com/vi/" + video["id"] + "/hqdefault.jpg"},
            "footer": {"text": "YouTube · " + video["published"]},
        }],
        "allowed_mentions": {"parse": [], "roles": [role_id] if role_id else []},
    }
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
    global last_check, failures
    seen = _load_seen()
    for channel_id in channel_ids:
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
        except (requests.RequestException, ET.ParseError, ValueError, OSError) as exc:
            failures += 1
            last_check = time.monotonic() + min(1800, INTERVAL * (2 ** min(failures, 4))) - INTERVAL
            warn_failure("YouTube " + channel_id, failures, exc)
            return
    failures = 0


def receive_websub_feed(xml_bytes):
    """Apply signed WebSub feed updates using the same RSS deduplication ledger."""
    root = ET.fromstring(xml_bytes)
    if root.tag != ATOM + "feed":
        raise ValueError("Unexpected WebSub feed")
    feed_channel = (root.findtext(YT + "channelId") or "").strip()
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
        for entry in root.findall(ATOM + "entry"):
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
