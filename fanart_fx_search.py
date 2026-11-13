"""Discover hashtagged fanart across public X posts via FxEmbed's documented search API.

Unlike creator profile feeds, this searches posts by *all authors*. Keep one
record per original X post, preserving artist credit and all photo URLs.
Failure never clears a previously collected candidate or blocks bot workers.
"""
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests
from fanart_discovery import BASE, QUEUE, TAGS, classify, read, write

STATE = BASE / "fanart_fx_search_state.json"
ENDPOINT = "https://api.fxtwitter.com/2/search"
_AUTHOR = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_POST = re.compile(r"^[0-9]{2,20}$")
MAX_PAGES = 2


def _photo_urls(post):
    media = post.get("media") if isinstance(post.get("media"), dict) else {}
    photos = media.get("photos") if isinstance(media.get("photos"), list) else []
    if not photos and isinstance(media.get("all"), list):
        photos = [p for p in media["all"] if isinstance(p, dict) and p.get("type") == "photo"]
    urls = []
    for photo in photos:
        if not isinstance(photo, dict) or photo.get("type", "photo") != "photo":
            continue
        url = photo.get("url")
        if not isinstance(url, str):
            continue
        parsed = urlsplit(url)
        if (parsed.scheme == "https" and parsed.hostname == "pbs.twimg.com"
                and parsed.path.startswith("/media/") and url not in urls):
            urls.append(url)
        if len(urls) >= 4:
            break
    return urls


def _record(group, tag, post):
    if not isinstance(post, dict) or post.get("type") not in (None, "status"):
        return None
    post_id = str(post.get("id") or "")
    author = post.get("author") if isinstance(post.get("author"), dict) else {}
    handle = author.get("screen_name") or author.get("username") or ""
    text = str(post.get("text") or "")
    if not _POST.fullmatch(post_id) or not _AUTHOR.fullmatch(str(handle)):
        return None
    if not re.search(r"(?<![\w])#" + re.escape(tag) + r"(?![\w])", text, re.I):
        return None
    if re.search(r"^\s*(?:RT\s+@|RT by\b|Repost by\b)", text, re.I):
        return None
    images = _photo_urls(post)
    if not images:
        return None
    decision = classify(text)
    return {"group": group, "hashtag": "#" + tag, "post_id": post_id,
            "post_url": f"https://x.com/{handle}/status/{post_id}",
            "artist": "@" + handle, "image_urls": images,
            "decision": decision,
            "review_status": "blocked" if decision == "blocked" else "pending",
            "source": "fxtwitter_hashtag_search",
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "text": text[:2000]}


def discover(session=None):
    """Collect validated fanart and record whether the search returned usable art.

    A successful HTTP 200 or FX code=200 is only API availability.  A usable
    hashtag result must also have an original post, matching tag, artist and
    a safe Twitter photo URL.  Empty results are NOT proof of X-wide coverage.
    """
    session = session or requests.Session()
    queue = read(QUEUE)
    previous_state = read(STATE)
    previous_groups = previous_state.get("groups", {})
    checked_at = datetime.now(timezone.utc).isoformat()
    added = 0
    results = {}
    for group, tag in TAGS.items():
        count = 0
        matched = 0
        returned = 0
        duplicates = 0
        pages = 0
        cursor = None
        seen_cursors = set()
        new_artists = set()
        existing_artists = {
            str(item.get("artist") or "").casefold()
            for item in queue.values()
            if isinstance(item, dict) and item.get("group") == group
        }
        earlier = previous_groups.get(group, {}) if isinstance(previous_groups, dict) else {}
        earlier = earlier if isinstance(earlier, dict) else {}
        last_nonempty_at = earlier.get("last_nonempty_at")
        last_new_artist_at = earlier.get("last_new_artist_at")
        last_http_status = None
        last_api_code = None
        error = None
        try:
            for _ in range(MAX_PAGES):
                params = {"q": "#" + tag, "feed": "media", "count": 50}
                if cursor:
                    params["cursor"] = cursor
                response = session.get(
                    ENDPOINT, params=params, timeout=(3, 12),
                    headers={"Accept": "application/json",
                             "User-Agent": "abyss-bot/1.0 (fanart)"})
                if isinstance(response.status_code, int):
                    last_http_status = response.status_code
                response.raise_for_status()
                body = response.json()
                last_api_code = body.get("code") if isinstance(body, dict) else None
                if not isinstance(body, dict) or last_api_code != 200:
                    raise ValueError("Search provider rejected response")
                posts = body.get("results")
                if not isinstance(posts, list):
                    raise ValueError("Search provider omitted results")
                pages += 1
                returned += len(posts)
                for post in posts:
                    item = _record(group, tag, post)
                    if item is None:
                        continue
                    matched += 1
                    key = group + ":" + item["post_id"]
                    if key in queue:
                        duplicates += 1
                        continue
                    if item["artist"].casefold() not in existing_artists:
                        new_artists.add(item["artist"].casefold())
                        existing_artists.add(item["artist"].casefold())
                    queue[key] = item
                    count += 1
                if count:
                    # Atomic persistence after every page preserves collected
                    # posts even when the next provider page fails.
                    write(QUEUE, queue)
                bottom = body.get("cursor")
                bottom = bottom.get("bottom") if isinstance(bottom, dict) else None
                if not posts or not isinstance(bottom, str) or not bottom or bottom in seen_cursors:
                    break
                seen_cursors.add(bottom)
                cursor = bottom
        except (requests.RequestException, ValueError, TypeError) as exc:
            error = type(exc).__name__
            logging.warning("FANART_FX_SEARCH_FAILED group=%s reason=%s", group, error)

        if matched:
            last_nonempty_at = checked_at
        if new_artists:
            last_new_artist_at = checked_at
        status = ("partial" if error and pages else "unavailable" if error
                  else "ok" if matched else "empty")
        result = {
            "status": status,
            "pages_fetched": pages,
            "http_status": last_http_status,
            "api_code": last_api_code,
            "returned_posts": returned,
            "matched": matched,
            "duplicate_posts": duplicates,
            "added": count,
            "new_artists": len(new_artists),
            "rejected_without_valid_photo_or_tag": max(0, returned - matched),
            "last_checked_at": checked_at,
            "last_nonempty_at": last_nonempty_at,
            "last_new_artist_at": last_new_artist_at,
            "coverage_verified": False,
        }
        if error:
            result["error"] = error
        results[group] = result
        added += count
    write(STATE, {
        "checked_at": checked_at,
        "groups": results,
        "coverage_verified": False,
        "total_added": added,
        "total_new_artists": sum(v["new_artists"] for v in results.values()),
    })
    logging.info("FANART_FX_SEARCH total_added=%d summary=%s", added, results)
    return {
        "status": "ok" if all(v["status"] in ("ok", "empty") for v in results.values())
                  else "partial",
        "added": added, "coverage_verified": False, "groups": results,
    }
