"""Additional X creator-owned originals from FxTwitter RSS.

Never treat reposted third-party tweets as a creator's own new post.
Only accept exact creator/status links, with a short freshness window to
prevent historical floods on first rollout.
"""
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests


def fetch_creator_originals(username, common, max_age_seconds=10800):
    url = "https://fxtwitter.com/" + username + "/feed.xml"
    response = requests.get(url, params={"count": 40},
                            timeout=7, headers={"User-Agent": "abyss-bot/1.0 (RSS polling)"})
    response.raise_for_status()
    if len(response.content) > 3_000_000:
        raise ValueError("Oversized FxTwitter feed")
    root = ET.fromstring(response.content)
    if root.tag != "rss":
        raise ValueError("Invalid FxTwitter RSS")
    channel = root.find("channel")
    if channel is None or ("@" + username).casefold() not in (channel.findtext("title") or "").casefold():
        raise ValueError("FxTwitter account mismatch")
    now = datetime.now(timezone.utc)
    results = []
    for item in channel.findall("item"):
        link = (item.findtext("link") or item.findtext("guid") or "").strip()
        parsed = urlsplit(link)
        match = re.fullmatch(r"/([^/]+)/status/([0-9]+)", parsed.path)
        if parsed.hostname not in ("x.com", "twitter.com") or not match:
            continue
        # Fx feeds include reposted fan-art authored by other users.
        if match.group(1).casefold() != username.casefold():
            continue
        published = item.findtext("pubDate") or ""
        try:
            stamp = parsedate_to_datetime(published)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            age = (now-stamp).total_seconds()
            if age < -300 or age > max_age_seconds:
                continue
        except (ValueError, TypeError, OverflowError):
            # No verified publish time: avoid mass replay of historical posts.
            continue
        title = item.findtext("title") or ""
        desc = item.findtext("description") or ""
        if not common._original_post(item) or __import__("x_repost_notifier").is_repost(title,desc):
            continue
        results.append({"id":match.group(2),"text":common._post_body(item),
                        "url":"https://x.com/"+username+"/status/"+match.group(2),
                        "source":url,"pub_date":published,
                        "images":common._post_images(item),"rss_title":title,"repost":False})
    logging.info("X_FX_FEED username=%s originals=%s",username,len(results))
    return results
