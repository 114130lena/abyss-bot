"""Randomly choose creator profile and banner images from independently cached sources.

Supported candidate sources: verified YouTube caches, and explicitly configured
X/CHZZK/Cafe image URLs. Set ABYSS_RANDOM_MEDIA=1 to enable. No platform
scraping or assumption of inaccessible private images.
"""
import json
import os
import random
from pathlib import Path
from urllib.parse import urlsplit

DATA_DIR = Path(os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
PROFILE_CACHE = DATA_DIR / "creator_profile_cache.json"
BANNER_CACHE = DATA_DIR / "youtube_banners.json"
LOCAL_CACHE = DATA_DIR / "creator_media_pool.json"

def _load(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}

def _valid(url):
    if not isinstance(url, str) or len(url) > 3000:
        return False
    p = urlsplit(url)
    return p.scheme == "https" and bool(p.hostname) and not p.username and not p.password

def _append(pool, source, url):
    if _valid(url) and not any(x["url"] == url for x in pool):
        pool.append({"source": source, "url": url})

def candidates(group, kind, default=""):
    """A source-qualified candidate list, not arbitrary images from posts."""
    if group not in ("hanayo", "sakihane") or kind not in ("profile", "banner"):
        return []
    result = []
    _append(result, "current", default)
    if kind == "profile":
        _append(result, "youtube", _load(PROFILE_CACHE).get(group))
    else:
        _append(result, "youtube", _load(BANNER_CACHE).get(group))
    external = _load(LOCAL_CACHE).get(group, {})
    if isinstance(external, dict):
        sources = external.get(kind, {})
        if isinstance(sources, dict):
            for source, urls in sources.items():
                if isinstance(urls, str):
                    urls = [urls]
                if isinstance(urls, list):
                    for url in urls[:30]:
                        _append(result, str(source), url)
    prefix = group.upper()
    for source in ("X", "CHZZK", "CAFE", "YOUTUBE"):
        key = f"{prefix}_{source}_{kind.upper()}_URL"
        _append(result, source.lower(), os.getenv(key, ""))
    return result

def choose(group, kind, default=""):
    items = candidates(group, kind, default)
    if not items:
        return default
    return random.SystemRandom().choice(items)["url"]

def enabled():
    return os.getenv("ABYSS_RANDOM_MEDIA", "0").strip().lower() in ("1", "true", "yes")
