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
    response=requests.get(url,params={"count":90},timeout=7,
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
        is_repost=author.casefold()!=username.casefold()
        title=item.findtext("title") or ""
        desc=item.findtext("description") or ""
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
    if known is None:
        _write_state(state_file,current_reposts)
        new_reposts=set()
        logging.info("X_FX_REPOST_SEEDED username=%s existing=%d",username,len(current_reposts))
    else:
        new_reposts=current_reposts-known
        # Keep the initial baseline unchanged. The notifier records delivery only
        # after Discord succeeds, so failed sends are eligible for retries.
        logging.info("X_FX_REPOST_SCAN username=%s visible=%d outside_baseline=%d",username,len(current_reposts),len(new_reposts))
    results=[v for k,v in entries.items() if not v["repost"] or k in new_reposts]
    logging.info("X_FX_FEED username=%s originals=%d reposts=%d",username,
                 sum(not p["repost"] for p in results),sum(p["repost"] for p in results))
    return results
