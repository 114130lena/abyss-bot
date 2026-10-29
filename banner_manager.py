"""Refresh official YouTube channel banners, keeping the last working URL.

YouTube's public page format is not a stable API; failures never stop alerts.
"""
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
import requests
from dotenv import load_dotenv

BASE_DIR = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
load_dotenv(BASE_DIR / ".env")
CACHE = BASE_DIR / "youtube_banners.json"
INTERVAL = max(3600, int(os.getenv("YOUTUBE_BANNER_REFRESH_SECONDS", "21600")))
CHANNELS = {
    "hanayo": "https://www.youtube.com/@HanayoHiyori",
    "sakihane": "https://www.youtube.com/@Sakihanechannel",
    "replay": "https://www.youtube.com/@Hiyorireplay",
}
ENV_OVERRIDES = {
    "hanayo": "HANAYO_BANNER_URL",
    "sakihane": "SAKIHANE_BANNER_URL",
    "replay": "HIYORI_REPLAY_BANNER_URL",
}
_lock = threading.RLock()
_last_attempt = {}
_cache = None

def _valid(url):
    if not isinstance(url, str) or len(url) > 3000:
        return False
    parsed = urlparse(url.replace("\\u0026", "&"))
    return parsed.scheme == "https" and parsed.hostname in (
        "yt3.googleusercontent.com", "yt3.ggpht.com", "yt3.google.com"
    )

def _read():
    global _cache
    with _lock:
        if _cache is None:
            try:
                value = json.loads(CACHE.read_text(encoding="utf-8"))
                _cache = value if isinstance(value, dict) else {}
            except (OSError, ValueError):
                _cache = {}
        return dict(_cache)

def _save(value):
    global _cache
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    temp = CACHE.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(CACHE)
    _cache = dict(value)

def _extract(page):
    # YouTube pageHeaderViewModel supplies banner.imageBannerViewModel.image.sources.
    # This is not a guaranteed stable interface; reject other thumbnails/avatars.
    anchors = [m.start() for m in re.finditer(r'"(?:imageBannerViewModel|banner)"\s*:', page)]
    for anchor in anchors:
        snippet = page[anchor:anchor + 14000]
        matches = re.findall(r'https:(?:\\/\\/|//)(?:yt3\\.googleusercontent\\.com|yt3\\.ggpht\\.com)[^"\\\s<>]*', snippet)
        for match in matches:
            url = match.replace("\\/", "/").replace("\\u0026", "&")
            if _valid(url):
                return url
    return None

def refresh_one(name, force=False):
    if name not in CHANNELS:
        return ""
    override = os.getenv(ENV_OVERRIDES[name], "").strip()
    if override.startswith("https://"):
        return override
    now = time.monotonic()
    with _lock:
        cached = _read()
        if not force and now - _last_attempt.get(name, -10**8) < INTERVAL:
            return cached.get(name, "")
        _last_attempt[name] = now
    try:
        response = requests.get(CHANNELS[name], timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; abyss-bot/1.0)"
        })
        response.raise_for_status()
        url = _extract(response.text)
        if not url:
            raise ValueError("YouTube banner metadata unavailable")
        with _lock:
            value = _read()
            if value.get(name) != url:
                value[name] = url
                _save(value)
                logging.info("YouTube banner updated: %s", name)
        return url
    except (requests.RequestException, ValueError, OSError) as exc:
        logging.warning("YouTube banner update failed (%s), using cached banner: %s", name, exc)
        return _read().get(name, "")

def get_banner(name):
    override = os.getenv(ENV_OVERRIDES.get(name, ""), "").strip()
    if override.startswith("https://"):
        return override
    return _read().get(name, "")

def run_banner_refresh():
    for name in CHANNELS:
        refresh_one(name)
    while True:
        time.sleep(INTERVAL)
        for name in CHANNELS:
            refresh_one(name, force=True)
