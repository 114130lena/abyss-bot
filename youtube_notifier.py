"""Free YouTube channel upload notifications using the official Atom feed.

Set YOUTUBE_CHANNEL_IDS=UC...,UC... in the server .env to enable.
"""
import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
load_dotenv(BASE_DIR / ".env")
CHANNEL_IDS = [v.strip() for v in os.getenv("YOUTUBE_CHANNEL_IDS", "").split(",") if v.strip()]
INTERVAL = max(60, int(os.getenv("YOUTUBE_POLL_INTERVAL", "300")))
WEBHOOK_URL = os.getenv("YOUTUBE_DISCORD_WEBHOOK_URL", os.getenv("DISCORD_WEBHOOK_URL", "")).strip()
SEEN_FILE = BASE_DIR / "youtube_seen_videos.json"
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
last_check = 0.0
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
        })
    return list(reversed(videos))


def _notify(video):
    payload = {
        "username": "유튜브 업로드 알림",
        "embeds": [{
            "title": video["title"][:256],
            "url": video["url"],
            "description": video["channel"] + " 채널에 새 영상이 올라왔습니다.",
            "color": 0xFF0000,
            "thumbnail": {"url": "https://i.ytimg.com/vi/" + video["id"] + "/hqdefault.jpg"},
            "footer": {"text": "YouTube · " + video["published"]},
        }],
        "allowed_mentions": {"parse": []},
    }
    response = session.post(WEBHOOK_URL, json=payload, timeout=15)
    response.raise_for_status()


def check_youtube():
    """Check configured channels; first sight of a channel does not alert for old uploads."""
    global last_check, failures
    if not CHANNEL_IDS:
        return
    now = time.monotonic()
    if now - last_check < INTERVAL:
        return
    last_check = now
    if not WEBHOOK_URL.startswith("https://"):
        logging.warning("YouTube: no valid Discord webhook configured")
        return
    seen = _load_seen()
    for channel_id in CHANNEL_IDS:
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
            logging.warning("YouTube %s check failed: %s", channel_id, exc)
            return
    failures = 0
