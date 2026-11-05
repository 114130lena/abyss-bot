"""Offline daily Hiyori X clip selection. No search/network/Discord writes.

Data-provider boundary deliberately separated: public hashtag search is currently
unreliable, so this module must not be scheduled until a verified discovery
provider supplies an exhaustive recent candidate list.
"""
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
DAILY_RECOMMENDATION_HOUR_KST = 12  # 12:00 PM (noon), not midnight
DAILY_RECOMMENDATION_MINUTE_KST = 0
TAG = re.compile(r"(?i)(?<![\w])#hiyoriclip_(?![\w])")
WEIGHTS = {"views": 0.40, "likes": 0.35, "reposts": 0.25}


def _metric(obj, key):
    value = obj.get(key)
    return value if type(value) is int and value >= 0 else None


def _posted_at(raw):
    try:
        timestamp = raw.get("created_timestamp")
        if isinstance(timestamp, (float, int)) and not isinstance(timestamp, bool):
            return datetime.fromtimestamp(timestamp, timezone.utc)
        stamp = raw.get("created_at")
        if isinstance(stamp, str):
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError, OSError, OverflowError):
        pass
    return None


def _video_key(raw):
    media = raw.get("media")
    if not isinstance(media, dict):
        return None
    videos = media.get("videos")
    if not isinstance(videos, list):
        return None
    for item in videos:
        if not isinstance(item, dict):
            continue
        vid = str(item.get("id") or "").strip()
        if vid:
            return "video:" + vid
        url = str(item.get("url") or "")
        parsed = urlsplit(url)
        if parsed.scheme == "https" and parsed.hostname == "video.twimg.com" and parsed.path.lower().endswith(".mp4"):
            return "url:" + parsed.path
    return None


def eligible(raw, now):
    if not isinstance(raw, dict):
        return False
    post_id = str(raw.get("id") or "")
    if not post_id.isdigit() or not TAG.search(str(raw.get("text") or "")):
        return False
    if raw.get("retweeted_by") or raw.get("reposted_by") or raw.get("type") in ("retweet", "repost"):
        return False
    created = _posted_at(raw)
    if created is None or not now-timedelta(hours=24) <= created < now:
        return False
    author = raw.get("author")
    if not isinstance(author, dict) or not re.fullmatch(r"[a-zA-Z0-9_]{1,15}",str(author.get("screen_name") or "")):
        return False
    if not _video_key(raw):
        return False
    # Likes and reposts are required; views can be hidden/unknown.
    return _metric(raw, "likes") is not None and _metric(raw, "reposts") is not None


def rank_clips(candidates, *, now=None, used_ids=()):
    """Rank true clips in the 24h ending at 12:00 noon Korea time.

    Per-metric log scaling avoids views numerically swamping engagement.
    Missing views do not silently become fabricated zero views. If not one
    candidate exposes views, redistribute weights across likes/reposts only.
    """
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        raise ValueError("Selection clock must be timezone-aware")
    now = now.astimezone(timezone.utc)
    seen_ids = set(str(i) for i in used_ids)
    rows = []
    videos_seen = set()
    for raw in candidates:
        if not eligible(raw, now) or str(raw["id"]) in seen_ids:
            continue
        video_key = _video_key(raw)
        if video_key in videos_seen:
            continue
        videos_seen.add(video_key)
        rows.append(raw)
    if not rows:
        return []
    maximum = {key:max((_metric(p,key) or 0) for p in rows) for key in WEIGHTS}
    active = [key for key in WEIGHTS if key != "views" or any(_metric(p,key) is not None for p in rows)]
    denominator = sum(WEIGHTS[key] for key in active)
    ranked=[]
    for item in rows:
        score=0.0
        for key in active:
            metric=_metric(item,key)
            if metric is None or maximum[key] == 0:
                continue
            score += (WEIGHTS[key]/denominator) * math.log1p(metric)/math.log1p(maximum[key])
        ranked.append({
            "id":str(item["id"]),
            "url":f"https://x.com/{item['author']['screen_name']}/status/{item['id']}",
            "author":"@"+item["author"]["screen_name"],
            "text":str(item.get("text") or ""),
            "video_key":_video_key(item),
            "created_at":_posted_at(item).isoformat(),
            "views":_metric(item,"views"),
            "likes":_metric(item,"likes"),
            "reposts":_metric(item,"reposts"),
            "score":round(score*100,2),
            "_raw_score":score,
        })
    ranked.sort(key=lambda x:(-x["_raw_score"],-x["likes"],-x["reposts"],x["id"]))
    for row in ranked:
        row.pop("_raw_score")
    return ranked


def select_one(candidates, *, now=None, used_ids=()):
    ranked = rank_clips(candidates, now=now, used_ids=used_ids)
    return ranked[0] if ranked else None
