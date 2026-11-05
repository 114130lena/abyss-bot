"""Read-only candidate screening for collected #hiyoriclip_ X video posts.

Does not contact Twitter, modify the collection, rank a daily winner, or
publish Discord messages. Always distinguishes metadata-verified video from
a video with a directly playable verified MP4 link.
"""
import argparse
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

TAG = re.compile(r"(?i)(?<!\w)#hiyoriclip_(?!\w)")
KST = ZoneInfo("Asia/Seoul")


def _date(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _number(value):
    return type(value) is int and value >= 0


def _mp4(value):
    try:
        url = urlsplit(value)
    except (TypeError, ValueError):
        return False
    return (url.scheme == "https" and url.hostname == "video.twimg.com"
            and url.username is None and url.password is None
            and url.path.lower().endswith(".mp4"))


def inspect(posts, *, now=None, hours=24, limit=30):
    now = (now or datetime.now(KST)).astimezone(timezone.utc)
    if hours <= 0 or limit <= 0:
        raise ValueError("Positive hours and limit required")
    if isinstance(posts, dict):
        posts = list(posts.values())
    counts = Counter()
    accepted = []
    for post in posts:
        counts["total"] += 1
        if not isinstance(post, dict) or not str(post.get("id", "")).isdigit():
            counts["invalid_post"] += 1
            continue
        created = _date(post.get("created_at"))
        if not created or not now - timedelta(hours=hours) <= created <= now:
            counts["outside_window"] += 1
            continue
        counts["in_window"] += 1
        if not TAG.search(str(post.get("text") or "")):
            counts["wrong_hashtag"] += 1
            continue
        if not (post.get("video_ids") or post.get("video_urls")):
            counts["no_video_metadata"] += 1
            continue
        counts["video_verified"] += 1
        direct_urls = [url for url in (post.get("video_urls") or []) if _mp4(url)]
        if direct_urls:
            counts["direct_mp4"] += 1
        else:
            counts["needs_video_url"] += 1
        for field in ("views", "likes", "reposts"):
            if _number(post.get(field)):
                counts[field + "_available"] += 1
            else:
                counts[field + "_missing"] += 1
        if all(_number(post.get(field)) for field in ("likes", "reposts")):
            counts["ranking_ready"] += 1
        else:
            counts["missing_core_metrics"] += 1
        accepted.append({
            "id": str(post["id"]),
            "author": str(post.get("author") or ""),
            "url": str(post.get("url") or ""),
            "created_at": created.isoformat(),
            "direct_mp4": bool(direct_urls),
            "views": post.get("views") if _number(post.get("views")) else None,
            "likes": post.get("likes") if _number(post.get("likes")) else None,
            "reposts": post.get("reposts") if _number(post.get("reposts")) else None,
            "source_count": len(post.get("discovered_via") or []),
        })
    accepted.sort(key=lambda item: item["created_at"], reverse=True)
    unique = []
    ids = set()
    for post in accepted:
        if post["id"] not in ids:
            unique.append(post)
            ids.add(post["id"])
    counts["duplicate_post_ids"] = len(accepted) - len(unique)
    return {
        "window_hours": hours,
        "checked_at": now.isoformat(),
        "metrics": dict(counts),
        "video_posts": len(unique),
        "sample_recent": unique[:limit],
        "coverage_note": "Partial account/RSS coverage; NOT global X hashtag search.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", default=str(
        Path(__file__).resolve().parent / "creator_media" / "hiyoriclip_posts.json"))
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    raw = json.loads(Path(args.index).read_text("utf-8"))
    data = raw.get("posts", {})
    if not isinstance(data, dict):
        raise ValueError("Malformed Hiyoriclip index")
    print("HIYORICLIP_SCREENING " + json.dumps(
        inspect(data, hours=args.hours, limit=args.limit), ensure_ascii=False))


if __name__ == "__main__":
    main()
