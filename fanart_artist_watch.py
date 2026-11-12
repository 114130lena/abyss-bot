"""Bounded discovery from known fan artists when global hashtag search is unavailable.

Known artist profiles are public, documented FxEmbed /2/profile/{handle}/media
and media.xml feeds. This is an *additional* discovery source, never a claim
to cover every hashtag author. Preserve X post provenance and gallery URLs.
"""
import html
import logging
import re
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from urllib.parse import quote
import xml.etree.ElementTree as ET

import requests
from fanart_discovery import BASE, QUEUE, TAGS, read, write
from fanart_fx_search import _record
from fanart_fx_rss import Images, POST

STATE = BASE / "fanart_artist_watch_state.json"
HANDLES = re.compile(r"^[A-Za-z0-9_]{1,15}$")
OFFICIAL = {"hanayohiyori", "sakihanehuya"}
MAX_ARTISTS_PER_GROUP = 3
MAX_RESULTS = 30


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _rss_posts(xml, artist):
    if len(xml) > 2_000_000:
        raise ValueError("RSS too large")
    root = ET.fromstring(xml)
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise ValueError("Malformed artist RSS")
    for node in channel.findall("item")[:MAX_RESULTS]:
        link = node.findtext("link") or node.findtext("guid") or ""
        match = POST.search(link)
        if not match or match.group(1).casefold() != artist.casefold():
            continue
        title = node.findtext("title") or ""
        body = node.findtext("description") or ""
        parser = _PlainText()
        parser.feed(body)
        images = Images()
        images.feed(body)
        for child in node:
            if child.tag.rsplit("}", 1)[-1] in ("enclosure", "content", "thumbnail"):
                images.add(child.attrib.get("url"))
        yield {"type": "status", "id": match.group(2),
               "author": {"screen_name": artist},
               "text": title + " " + " ".join(parser.parts),
               "media": {"photos": [{"type": "photo", "url": u}
                                    for u in images.urls[:4]]}}


def _fetch_artist(group, tag, artist, session):
    # Prefer FxEmbed's documented JSON media timeline. On network/upstream
    # failure only, try its separately documented RSS media feed.
    endpoint = f"https://api.fxtwitter.com/2/profile/{quote(artist)}/media"
    try:
        response = session.get(endpoint, params={"count": MAX_RESULTS},
                               timeout=(3, 11), headers={"Accept": "application/json"})
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get("code") != 200 or not isinstance(body.get("results"), list):
            raise ValueError("Invalid Fx profile media page")
        posts = body["results"]
        source = "fx_artist_media_api"
    except (requests.RequestException, ValueError, TypeError):
        response = session.get(f"https://fxtwitter.com/{quote(artist)}/media.xml",
                               params={"count": MAX_RESULTS, "safe": 1},
                               timeout=(3, 10), headers={"Accept": "application/xml"})
        response.raise_for_status()
        posts = list(_rss_posts(response.content, artist))
        source = "fx_artist_media_rss"
    results = []
    for post in posts:
        item = _record(group, tag, post)
        if item is None or item["artist"].casefold() != "@" + artist.casefold():
            continue
        item["source"] = source
        results.append(item)
    return results


def discover(session=None, max_artists=MAX_ARTISTS_PER_GROUP):
    session = session or requests.Session()
    queue = read(QUEUE)
    state = read(STATE)
    groups = {}
    total = 0
    for group, tag in TAGS.items():
        artists = sorted({artist for item in queue.values()
                          if isinstance(item, dict) and item.get("group") == group
                          for artist in [str(item.get("artist") or "").lstrip("@")]
                          if HANDLES.fullmatch(artist) and artist.casefold() not in OFFICIAL},
                         key=str.casefold)
        previous = state.get(group) if isinstance(state.get(group), dict) else {}
        start = int(previous.get("cursor", 0)) % len(artists) if artists else 0
        limit = min(max(0, int(max_artists)), len(artists))
        # Reserve at most one slot for a newly discovered artist (<24h);
        # keep other slots round-robin so established artists are not starved.
        now = datetime.now(timezone.utc)
        recent = []
        for item in queue.values():
            if not isinstance(item, dict) or item.get("group") != group:
                continue
            artist = str(item.get("artist") or "").lstrip("@")
            if not HANDLES.fullmatch(artist) or artist.casefold() in OFFICIAL:
                continue
            stamp = item.get("discovered_at")
            try:
                moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if moment.tzinfo and timedelta(0) <= now - moment <= timedelta(hours=24):
                    recent.append((moment, artist))
            except (AttributeError, ValueError, TypeError, OverflowError):
                continue
        recent.sort(key=lambda x: x[0], reverse=True)
        selected = [recent[0][1]] if recent and limit else []
        rotated = [artists[(start + i) % len(artists)] for i in range(len(artists))]
        selected.extend(a for a in rotated if a.casefold() not in {v.casefold() for v in selected})
        selected = selected[:limit]
        advance = max(1, limit - bool(recent)) if limit else 0
        added = 0
        success = 0
        for artist in selected:
            try:
                posts = _fetch_artist(group, tag, artist, session)
                success += 1
                for item in posts:
                    key = group + ":" + item["post_id"]
                    if key not in queue:
                        queue[key] = item
                        added += 1
                if posts and added:
                    write(QUEUE, queue)
            except (requests.RequestException, ValueError, TypeError, ET.ParseError) as exc:
                logging.warning("FANART_ARTIST_WATCH_FAILED group=%s reason=%s",
                                group, type(exc).__name__)
        groups[group] = {"known_artists": len(artists), "checked": len(selected),
                         "successful": success, "added": added,
                         "status": ("no_known_artists" if not artists else
                                    "ok" if success == len(selected) else "partial")}
        total += added
        state[group] = {"cursor": ((start + advance) % len(artists) if artists else 0)}
    state["checked_at"] = datetime.now(timezone.utc).isoformat()
    state["groups"] = groups
    write(STATE, state)
    logging.info("FANART_ARTIST_WATCH added=%d groups=%s", total, groups)
    return {"added": total, "groups": groups}
