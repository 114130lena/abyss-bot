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

# Local collected-image randomizer. The 10 verified files are the only pool.
MEDIA_ROOT = DATA_DIR / "creator_media"
MEDIA_INDEX = MEDIA_ROOT / "index.json"
MEDIA_LAST = MEDIA_ROOT / "last_used.json"

def _local_candidates(group, kind):
    if group not in ("hanayo", "sakihane") or kind not in ("profile", "banner"):
        return []
    index = _load(MEDIA_INDEX)
    entries = index.get(group, {}).get(kind, [])
    found = []
    for item in entries if isinstance(entries, list) else []:
        if not isinstance(item, dict):
            continue
        rel = item.get("path", "")
        if not isinstance(rel, str):
            continue
        path = (MEDIA_ROOT / rel).resolve()
        # Never allow an index entry to escape the collector's data folder.
        if MEDIA_ROOT.resolve() not in path.parents or not path.is_file():
            continue
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp") or path.stat().st_size > 12*1024*1024:
            continue
        found.append((path, item.get("sha256", path.name)))
    if os.getenv("ABYSS_FANART_ENABLED", "0") == "1":
        fanart = _load(MEDIA_ROOT / "fanart_index.json").get(group, [])
        for item in fanart if isinstance(fanart, list) else []:
            if not isinstance(item, dict) or item.get("approved") is not True or item.get("permission_confirmed") is not True:
                continue
            if item.get("hashtag") != ("#hiyoriart_" if group == "hanayo" else "#huyart"):
                continue
            rel = item.get("profile_path" if kind == "profile" else "path", "")
            allowed_prefix = ("fanart_profiles/",) if kind == "profile" else ("fanart/", "fanart_banners/")
            if not isinstance(rel, str) or not rel.startswith(allowed_prefix):
                continue
            path = (MEDIA_ROOT / rel).resolve()
            if MEDIA_ROOT.resolve() not in path.parents or not path.is_file() or path.stat().st_size > 12*1024*1024:
                continue
            found.append((path, item.get("sha256", path.name)))
    return found

def choose_local(group, kind):
    """Prefer a different image than the previous successfully delivered alert."""
    pool = _local_candidates(group, kind)
    if not pool:
        return None
    previous = _load(MEDIA_LAST).get(group, {}).get(kind)
    allowed = [item for item in pool if item[1] != previous]
    return random.SystemRandom().choice(allowed or pool)

def mark_delivered(group, selected):
    from resilience import save_json
    index = _load(MEDIA_LAST)
    values = index.setdefault(group, {})
    for kind, (_, sha) in selected.items():
        values[kind] = sha
    save_json(MEDIA_LAST, index)

def prepare_payload(payload):
    """Replace header avatar and footer banner with fresh local attachment paths.

    Return (clean_payload, selected). No metadata keys enter the Discord API.
    """
    import copy
    output = copy.deepcopy(payload)
    containers = [c for c in output.get("components",[]) if c.get("type") == 17]
    if not containers:
        return output, None
    parts = containers[-1].get("components", [])
    section = next((p for p in parts if p.get("type")==9 and p.get("accessory",{}).get("type")==11),None)
    if not section:
        return output, None
    text_value = " ".join(c.get("content","") for c in section.get("components",[]))
    key = text_value.casefold()
    group = ("hanayo" if "히요리" in key or "hanayo" in key else
             "sakihane" if "후야" in key or "sakihane" in key else "")
    if not group:
        return output, None
    selected = {}
    for kind in ("profile","banner"):
        picked = choose_local(group,kind)
        if not picked:
            return output,None
        selected[kind] = picked
    # Attribute consent-approved fan art in the footer when selected.
    chosen_banner = selected["banner"][0]
    if any(p in ("fanart","fanart_banners") for p in chosen_banner.relative_to(MEDIA_ROOT).parts):
        info = _load(MEDIA_ROOT / "fanart_index.json").get(group, [])
        record = next((v for v in info if isinstance(v, dict) and v.get("sha256") == selected["banner"][1]), None)
        if not record or not record.get("permission_confirmed"):
            return payload, None
        credit = "-# 🎨 FAN ART · " + record["artist"] + " · [원본 게시물](" + record["post_url"] + ")"
        for part in parts:
            if part.get("type") == 10 and "OFFICIAL LINKS" in part.get("content", ""):
                part["content"] += "\n" + credit
                break
    section["accessory"]["media"]["url"] = "attachment://abyss_profile" + selected["profile"][0].suffix
    # Only the gallery immediately following the official links signature is the footer.
    for i, part in enumerate(parts[:-1]):
        if part.get("type")==10 and "OFFICIAL LINKS" in part.get("content",""):
            gallery=parts[i+1]
            if gallery.get("type")==12:
                gallery["items"]=[{"media":{"url":"attachment://abyss_banner"+selected["banner"][0].suffix},
                                   "description":"수집된 공식 채널 배너"}]
                return output,(group,selected)
    return payload,None
