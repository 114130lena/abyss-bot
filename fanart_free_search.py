"""Free, no-login, low-frequency fanart discovery from public RSS search indexes.

These search indexes are incomplete. Only an original X post with verified
hashtags and direct Twitter photo URLs may enter the canonical queue. Never
interpret a search-index link alone as proof of an eligible fanart image.
No X login, private API, bypass, subscription or paid endpoint is used.
"""
import html
import logging
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit
import xml.etree.ElementTree as ET

import requests
from fanart_discovery import BASE, QUEUE, TAGS, read, write
from fanart_fx_search import _record

STATE = BASE / "fanart_free_sources_state.json"
POST = re.compile(r"https?://(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})/status/(\d{2,20})(?:[/?#]|$)", re.I)
MAX_ITEMS = 20
MAX_FETCHES = 12
MAX_XML_BYTES = 1_000_000
# Public instances are unstable; allow only named operators' explicit hosts.
NITTER_HOSTS = {"nitter.mie.foundation", "nitter.mizuki.st", "nitter.jaydenha.uk"}
HEADERS = {"User-Agent": "abyss-bot/1.0 (+public-feed; infrequent requests)"}


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(html.unescape(href))


def _rss(content):
    if len(content) > MAX_XML_BYTES:
        raise ValueError("RSS response too large")
    root = ET.fromstring(content)
    if root.tag != "rss":
        raise ValueError("Unexpected RSS format")
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS channel missing")
    return channel.findall("item")[:MAX_ITEMS]


def _links(item):
    values = [item.findtext("link") or "", item.findtext("guid") or ""]
    text = (item.findtext("description") or "")[:15000]
    parser = _Links()
    parser.feed(text)
    values += parser.links
    return values


def _x_post_url(value, nitter_host=None):
    if not isinstance(value, str):
        return None
    value = html.unescape(value)
    p = urlsplit(value)
    # Search RSS aggregators sometimes wrap the original link as an URL
    # query; never follow redirectors or untrusted intermediate hosts.
    if p.scheme != "https":
        return None
    if p.hostname == nitter_host and nitter_host in NITTER_HOSTS:
        # RSS search can link to the instance's local status permalink.
        value = "https://x.com" + p.path
        p = urlsplit(value)
    if p.hostname not in ("x.com", "www.x.com", "twitter.com", "www.twitter.com"):
        return None
    match = POST.fullmatch(p.scheme + "://" + p.netloc + p.path)
    return (match.group(1), match.group(2)) if match else None


def _source_urls(tag):
    # A simple web-index query. RSS feeds are only hints; verify every hit.
    q = quote('site:x.com/ "' + "#" + tag + '"', safe="")
    return {
        "bing_index": "https://www.bing.com/search?q=" + q + "&format=rss",
        "google_news_index": "https://news.google.com/rss/search?q=" + q,
    }


def _fetch_status(session, handle, post_id):
    # Public FxEmbed single-post endpoint, not its broken /2/search route.
    url = f"https://api.fxtwitter.com/{handle}/status/{post_id}"
    response = session.get(url, timeout=(3, 9), headers=HEADERS,
                           allow_redirects=False)
    response.raise_for_status()
    if len(response.content) > MAX_XML_BYTES:
        raise ValueError("Post response too large")
    data = response.json()
    post = data.get("tweet") if isinstance(data, dict) else None
    if not isinstance(post, dict):
        return None
    author = post.get("author") if isinstance(post.get("author"), dict) else {}
    claimed = author.get("screen_name") or author.get("username") or ""
    if str(post.get("id") or "") != post_id or str(claimed).casefold() != handle.casefold():
        return None
    return post


def _discover_index(name, endpoint, group, tag, session, queue, seen_ids, allowance):
    response = session.get(endpoint, timeout=(3, 10), headers=HEADERS,
                           allow_redirects=False)
    response.raise_for_status()
    examined, added, duplicates, verified = 0, 0, 0, 0
    for item in _rss(response.content):
        if allowance[0] >= MAX_FETCHES:
            break
        for candidate in _links(item):
            post = _x_post_url(candidate, urlsplit(endpoint).hostname if name.startswith("nitter_") else None)
            if not post:
                continue
            handle, post_id = post
            key = group + ":" + post_id
            examined += 1
            if key in queue or key in seen_ids:
                duplicates += 1
                break
            seen_ids.add(key)
            allowance[0] += 1
            try:
                data = _fetch_status(session, handle, post_id)
                art = _record(group, tag, data) if data else None
                if not art:
                    break
                verified += 1
                art["source"] = name + "_verified_status"
                queue[key] = art
                added += 1
            except (requests.RequestException, ValueError, TypeError) as exc:
                logging.info("FANART_FREE_STATUS_UNAVAILABLE provider=%s reason=%s",
                             name, type(exc).__name__)
            break
    if added:
        write(QUEUE, queue)
    return {"status": "ok", "examined": examined, "added": added,
            "duplicates": duplicates, "verified": verified}


def _nitter_feeds():
    values = os.getenv("FANART_NITTER_SEARCH_HOSTS", "nitter.mie.foundation").split(",")
    hosts = [v.strip().lower() for v in values if v.strip()]
    return [v for v in hosts if v in NITTER_HOSTS][:2]


def discover(session=None):
    """One slow, bounded pass with global post-ID dedup across all sources."""
    session = session or requests.Session()
    queue = read(QUEUE)
    results = {}
    seen_ids = set()
    allowance = [0]
    for group, tag in TAGS.items():
        results[group] = {}
        urls = _source_urls(tag)
        for host in _nitter_feeds():
            urls["nitter_" + host] = (
                "https://" + host + "/search/rss?f=tweets&q="
                + quote("#" + tag + " filter:images", safe=""))
        for source, endpoint in urls.items():
            try:
                results[group][source] = _discover_index(
                    source, endpoint, group, tag, session, queue,
                    seen_ids, allowance)
            except (requests.RequestException, ET.ParseError, ValueError, OSError) as exc:
                logging.warning("FANART_FREE_SOURCE_UNAVAILABLE group=%s provider=%s reason=%s",
                                group, source, type(exc).__name__)
                results[group][source] = {"status": "unavailable", "added": 0,
                                          "error": type(exc).__name__}
    # Monitoring never claims completeness; web-index snippets can omit
    # newest posts and a successful RSS fetch may have zero usable X links.
    status = {"checked_at": datetime.now(timezone.utc).isoformat(),
              "groups": results, "coverage_verified": False,
              "status": "partial_index_only",
              "post_lookups": allowance[0]}
    write(STATE, status)
    return status
