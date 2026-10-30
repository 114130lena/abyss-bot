"""Shared official creator avatars and verified channel handles.

Avatar data is refreshed opportunistically; failures never interrupt notifications.
"""
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import requests

BASE_DIR = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
CACHE_FILE = BASE_DIR / "creator_profile_cache.json"
CHANNELS = {
    "hanayo": ("https://www.youtube.com/@HanayoHiyori", "@HanayoHiyori"),
    "sakihane": ("https://www.youtube.com/@Sakihanechannel", "@Sakihanechannel"),
}
_lock = threading.RLock()
_last_refresh = {}
INTERVAL = 21600


def handle(group):
    return CHANNELS.get(group, ("", ""))[1]


def _read():
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def avatar(group):
    with _lock:
        return str(_read().get(group, "") or "")


def _extract_avatar(page):
    # Prefer avatar-specific metadata, not the first thumbnail on the page.
    page = page.replace(chr(92) + "/", "/").replace("\u0026", "&")
    for marker in ('"avatarViewModel"', '"avatar"', '"channelAvatar"'):
        pos = page.find(marker)
        if pos < 0:
            continue
        snippet = page[pos:pos + 5500]
        urls = re.findall(r'https://(?:yt3\.googleusercontent\.com|yt3\.ggpht\.com)/[^"\s<>]+', snippet)
        if urls:
            return urls[0].replace("&amp;", "&")
    return ""


def refresh(group, force=False):
    if group not in CHANNELS:
        return ""
    with _lock:
        now = time.monotonic()
        if not force and now - _last_refresh.get(group, -10**9) < INTERVAL:
            return avatar(group)
        _last_refresh[group] = now
    try:
        response = requests.get(CHANNELS[group][0], timeout=12,
                                headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        image_url = _extract_avatar(response.text)
        if not image_url:
            raise ValueError("Official channel avatar metadata unavailable")
        with _lock:
            cached = _read()
            if cached.get(group) != image_url:
                cached[group] = image_url
                CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                temporary = CACHE_FILE.with_suffix(".tmp")
                temporary.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
                temporary.replace(CACHE_FILE)
        return image_url
    except (requests.RequestException, OSError, ValueError) as exc:
        logging.warning("Creator avatar refresh failed for %s; using cache: %s", group, exc)
        return avatar(group)


def refresh_all():
    for group in CHANNELS:
        refresh(group, force=True)
