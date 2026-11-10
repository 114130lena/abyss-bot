"""Monitor creator's FxTwitter timeline for originals AND reposts.

A repost's publication time is the original author's timestamp, not the repost
event. New appearances are detected with a persistent timeline membership state.
First successful fetch seeds existing items (no historical notification flood).
"""
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests

def _state_path(username):
    root=Path(os.getenv("BOT_DATA_DIR","/home/ubuntu/chzzk-bot"))
    return root / ("x_fx_timeline_" + re.sub(r"[^A-Za-z0-9_]","",username).lower() + ".json")

def _read_state(path):
    try:
        saved=json.loads(path.read_text(encoding="utf-8"))
        if isinstance(saved,list):
            return set(str(v) for v in saved)
    except (OSError,ValueError,TypeError):
        pass
    return None

def _write_state(path, ids):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(ids,key=int)[-3000:]),encoding="utf-8")
    tmp.replace(path)

def fetch_creator_originals(username, common, max_age_seconds=10800):
    """Preserves legacy entrypoint; returns own posts and new off-author reposts."""
    url="https://fxtwitter.com/"+username+"/feed.xml"
    response=requests.get(url,params={"count":90},timeout=(2.5, 5),
                          headers={"User-Agent":"abyss-bot/1.0 (RSS polling)"})
    response.raise_for_status()
    if len(response.content)>3_000_000:
        raise ValueError("Oversized FxTwitter feed")
    root=ET.fromstring(response.content)
    if root.tag!="rss":
        raise ValueError("Invalid FxTwitter RSS")
    channel=root.find("channel")
    if channel is None or ("@"+username).casefold() not in (channel.findtext("title") or "").casefold():
        raise ValueError("FxTwitter account mismatch")
    now=datetime.now(timezone.utc)
    entries={}
    for item in channel.findall("item"):
        link=(item.findtext("link") or item.findtext("guid") or "").strip()
        parsed=urlsplit(link)
        m=re.fullmatch(r"/([^/]+)/status/([0-9]+)",parsed.path)
        if parsed.hostname not in ("x.com","twitter.com") or not m:
            continue
        author,post_id=m.groups()
        title=item.findtext("title") or ""
        desc=item.findtext("description") or ""
        from x_post_classification import classify
        kind = classify({"rss_title": title,
                         "rss_description": desc, "url": link}, username)
        is_repost = kind == "repost"
        if kind == "reply":
            continue
        if not is_repost and not common._original_post(item):
            continue
        published=item.findtext("pubDate") or ""
        if not is_repost:
            try:
                stamp=parsedate_to_datetime(published)
                if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
                age=(now-stamp).total_seconds()
                if age < -300 or age>max_age_seconds:continue
            except (ValueError,TypeError,OverflowError):
                continue
        entries[post_id]={"id":post_id,"text":common._post_body(item),
                          "url":"https://x.com/"+author+"/status/"+post_id,
                          "source":url,"pub_date":published,
                          "images":common._post_images(item),
                          "rss_title":title,"repost":is_repost}
    current_reposts={k for k,v in entries.items() if v["repost"]}
    state_file=_state_path(username)
    known=_read_state(state_file)
    try:
        seconds_since_baseline = now.timestamp() - state_file.stat().st_mtime
    except OSError:
        seconds_since_baseline = float("inf")
    if known is None or seconds_since_baseline > 6 * 3600:
        # An old Fx baseline is NOT a reliable repost-event timestamp.
        # Re-seed after a long feed outage/restart rather than broadcasting
        # days-old original X posts as if they were freshly reposted.
        _write_state(state_file,current_reposts)
        new_reposts=set()
        logging.warning("X_FX_REPOST_REBASELINE username=%s visible=%d baseline_age_hours=%s",
                        username,len(current_reposts),
                        round(seconds_since_baseline / 3600, 1) if seconds_since_baseline != float("inf") else "missing")
    else:
        new_reposts=current_reposts-known
        # Refresh the feed snapshot timestamp on EVERY successful scan.
        # Do NOT mark new items as seen here: the Discord notifier is the
        # only layer allowed to mark a repost delivered after send succeeds.
        _write_state(state_file,current_reposts & known)
        logging.info("X_FX_REPOST_SCAN username=%s visible=%d outside_baseline=%d",
                     username,len(current_reposts),len(new_reposts))
    results=[v for k,v in entries.items() if not v["repost"] or k in new_reposts]
    logging.info("X_FX_FEED username=%s originals=%d reposts=%d",username,
                 sum(not p["repost"] for p in results),sum(p["repost"] for p in results))
    return results


# Bluebird/Twscraper only collects events. The existing X notification worker
# owns all Discord sends, shared idempotency, media validation and thread routing.
BLUEBIRD_BRIDGE_PATH = Path(
    os.getenv("X_BLUEBIRD_BRIDGE_PATH", "/home/ubuntu/bluebird-shadow/data/bluebird_bridge_events.json")
)
_BLUEBIRD_CREATORS = {"hanayohiyori", "sakihanehuya"}
_BLUEBIRD_STATUS = re.compile(r"^https://x\.com/([A-Za-z0-9_]{1,15})/status/([0-9]+)$")
_BLUEBIRD_MAX_AGE = 7200
_BLUEBIRD_MAX_STALENESS = 240


def fetch_bluebird_events(username, now=None):
    """Return validated locally collected X events, or None if unavailable.

    Fail closed on corrupt/untrusted/stale data. The first sidecar fetch
    *never* produces events; the reader does not generate any event IDs.
    """
    if os.getenv("X_BLUEBIRD_BRIDGE_ENABLED", "1").strip().lower() in {"0", "false", "off", "no"}:
        return None
    account = str(username or "").lstrip("@").casefold()
    if account not in _BLUEBIRD_CREATORS:
        return None
    now = int(now if now is not None else __import__("time").time())
    try:
        if BLUEBIRD_BRIDGE_PATH.stat().st_size > 1_500_000:
            raise ValueError("oversize snapshot")
        payload = json.loads(BLUEBIRD_BRIDGE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        logging.warning("X_BRIDGE_UNAVAILABLE reason=%s", type(exc).__name__)
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        logging.warning("X_BRIDGE_UNAVAILABLE invalid_schema")
        return None
    updated = payload.get("updated_at")
    if not isinstance(updated, int) or not 0 <= now-updated <= _BLUEBIRD_MAX_STALENESS:
        logging.info("X_BRIDGE_STALE creator=%s", account)
        return None
    users = payload.get("users")
    if not isinstance(users, dict) or not isinstance(users.get(account), list):
        return None
    result = []
    for post in users[account][:250]:
        if not isinstance(post, dict):
            continue
        event_id = str(post.get("id") or "")
        created = post.get("created_at")
        if not event_id.isdigit() or not isinstance(created, int):
            continue
        age = now - created
        if age < -300 or age > _BLUEBIRD_MAX_AGE:
            continue
        owner_url = _BLUEBIRD_STATUS.fullmatch(str(post.get("url") or ""))
        target_url = _BLUEBIRD_STATUS.fullmatch(str(post.get("source_link") or ""))
        if not owner_url or owner_url.group(1).casefold() != account or owner_url.group(2) != event_id:
            continue
        if not target_url:
            continue
        repost = post.get("repost") is True
        if post.get("repost") not in (True, False) or post.get("reply") is not False:
            continue
        if repost and (
            target_url.group(1).casefold() == account
            or target_url.group(2) == event_id
        ):
            continue
        if not repost and (target_url.group(1).casefold() != account or target_url.group(2) != event_id):
            continue
        text = post.get("text")
        title = post.get("rss_title")
        date = post.get("pub_date")
        if not all(isinstance(value, str) for value in (text, title, date)):
            continue
        images = post.get("images")
        if not isinstance(images, list):
            continue
        clean_images = []
        for value in images[:4]:
            if not isinstance(value, str):
                continue
            parsed = urlsplit(value)
            if parsed.scheme == "https" and parsed.hostname == "pbs.twimg.com" and parsed.path.startswith("/media/"):
                clean_images.append(value)
        video = post.get("bluebird_video_url")
        parsed_video = urlsplit(video) if isinstance(video, str) else None
        if not (
            parsed_video and parsed_video.scheme == "https"
            and parsed_video.hostname == "video.twimg.com"
            and parsed_video.path.lower().endswith((".mp4", ".webm"))
        ):
            video = ""
        result.append({
            "id": event_id, "text": text[:2800], "url": owner_url.group(0),
            "source_link": target_url.group(0), "pub_date": date,
            "source": "bluebird-twscraper", "images": clean_images,
            "bluebird_video_url": video,
            "bluebird_media_verified": post.get("bluebird_media_verified") is True,
            "rss_title": title[:300], "rss_description": "",
            "repost": repost, "reply": False,
        })
    logging.info("X_BRIDGE_READ creator=%s events=%d", account, len(result))
    return sorted(result, key=lambda item: int(item["id"]))
