"""One-shot, read-only Fx hashtag validation against the production fanart queue.

Never writes source/queue state or Discord messages. Never outputs artist handles,
post IDs, post text, raw response bodies or authentication credentials.
"""
import json
import time
from datetime import datetime, timezone

import requests
from fanart_discovery import QUEUE, TAGS, read
from fanart_fx_search import ENDPOINT, _record

HEADERS = {"Accept": "application/json",
           "User-Agent": "abyss-bot/1.0 (read-only fanart audit)"}


def probe(session=None):
    session = session or requests.Session()
    queue = read(QUEUE)
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    groups = {}
    for group, tag in TAGS.items():
        known = {str(value.get("artist") or "").casefold()
                 for value in queue.values()
                 if isinstance(value, dict) and value.get("group") == group}
        started = time.monotonic()
        item = {"status": "unavailable", "http_status": None,
                "api_code": None, "returned_posts": 0,
                "valid_photo_posts": 0, "queued_valid_posts": 0,
                "not_yet_queued_posts": 0, "unknown_artists_found": 0,
                "elapsed_seconds": None, "coverage_verified": False}
        try:
            response = session.get(
                ENDPOINT, params={"q": "#" + tag, "feed": "media", "count": 50},
                headers=HEADERS, timeout=(3, 12), allow_redirects=False)
            if isinstance(response.status_code, int):
                item["http_status"] = response.status_code
            response.raise_for_status()
            data = response.json()
            item["api_code"] = data.get("code") if isinstance(data, dict) else None
            if not isinstance(data, dict) or item["api_code"] != 200:
                raise ValueError("Provider search code not 200")
            posts = data.get("results")
            if not isinstance(posts, list):
                raise ValueError("Provider search omitted results")
            item["returned_posts"] = len(posts)
            good = [_record(group, tag, post) for post in posts]
            good = [post for post in good if post is not None]
            item["valid_photo_posts"] = len(good)
            item["queued_valid_posts"] = sum(group + ":" + p["post_id"] in queue
                                             for p in good)
            item["not_yet_queued_posts"] = len(good) - item["queued_valid_posts"]
            item["unknown_artists_found"] = len({
                p["artist"].casefold() for p in good
                if p["artist"].casefold() not in known})
            item["status"] = "valid_results" if good else "empty_or_not_eligible"
        except (requests.RequestException, TypeError, ValueError) as exc:
            item["error_type"] = type(exc).__name__
        item["elapsed_seconds"] = round(time.monotonic() - started, 2)
        groups[group] = item
    return {"checked_at": checked_at, "read_only": True, "sent_discord": False,
            "search_coverage_verified": False, "groups": groups}


if __name__ == "__main__":
    print("FANART_HASHTAG_LIVE_PROBE " + json.dumps(
        probe(), ensure_ascii=False, separators=(",", ":")))
