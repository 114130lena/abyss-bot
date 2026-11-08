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
    session = session or requests.Session()
    queue = read(QUEUE)
    added = 0
    results = {}
    for group, tag in TAGS.items():
        count = 0
        matched = 0
        cursor = None
        seen_cursors = set()
        try:
            for _ in range(MAX_PAGES):
                params = {"q": "#" + tag, "feed": "media", "count": 50}
                if cursor:
                    params["cursor"] = cursor
                response = session.get(ENDPOINT, params=params, timeout=(3, 12),
                                       headers={"Accept": "application/json",
                                                "User-Agent": "abyss-bot/1.0 (fanart)"})
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict) or body.get("code") != 200:
                    raise ValueError("Search provider rejected response")
                posts = body.get("results")
                if not isinstance(posts, list):
                    raise ValueError("Search provider omitted results")
                for post in posts:
                    item = _record(group, tag, post)
                    if item is None:
                        continue
                    matched += 1
                    key = group + ":" + item["post_id"]
                    if key not in queue:
                        queue[key] = item
                        count += 1
                # Persist every page so a later provider outage loses no entries.
                if count:
                    write(QUEUE, queue)
                bottom = body.get("cursor")
                bottom = bottom.get("bottom") if isinstance(bottom, dict) else None
                if not posts or not isinstance(bottom, str) or not bottom or bottom in seen_cursors:
                    break
                seen_cursors.add(bottom)
                cursor = bottom
            results[group] = {"matched": matched, "added": count, "status": "ok"}
        except (requests.RequestException, ValueError, TypeError) as exc:
            logging.warning("FANART_FX_SEARCH_FAILED group=%s reason=%s", group, type(exc).__name__)
            results[group] = {"matched": matched, "added": count,
                              "status": "unavailable", "error": type(exc).__name__}
        added += count
    write(STATE, {"checked_at": datetime.now(timezone.utc).isoformat(), "groups": results})
    logging.info("FANART_FX_SEARCH total_added=%d result=%s", added, results)
    return {"status": "ok" if all(x["status"] == "ok" for x in results.values())
            else "partial", "added": added, "groups": results}
