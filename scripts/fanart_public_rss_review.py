"""Discover fanart metadata through public Nitter hashtag RSS, for review only.

Never auto-approves/reposts content. No X API token, no extra Discord messages.
Uses the existing fanart discovery review queue and media verification service.
"""
import html
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit
import xml.etree.ElementTree as ET
import requests

from fanart_discovery import QUEUE, TAGS, read, write, classify
from x_post_media import resolve_media

HOSTS=("nitter.mie.foundation","nitter.meowing.monster")
MAX_PER_TAG=15
MAX_PER_RUN=25
PARSER_RE=re.compile(r"^/([A-Za-z0-9_]{1,15})/status/(\d{16,21})(?:/.*)?$")
HTML_TAGS=re.compile(r"<[^>]+>")

def candidates_from_rss(content,group,hashtag):
    root=ET.fromstring(content)
    if root.tag!="rss":
        raise ValueError("Not an RSS feed")
    items=root.findall("./channel/item")
    results=[]
    seen=set()
    for entry in items:
        title=entry.findtext("title") or ""
        description=entry.findtext("description") or ""
        text=html.unescape(HTML_TAGS.sub(" ",title+" "+description))
        if not re.search(r"(?i)(?<![\w])#"+re.escape(hashtag)+r"(?![\w])",text):
            continue
        # Search results labeled as reposts still link to the original artist.
        # Keep the artist's source post in the permission-review queue.
        link=entry.findtext("link") or ""
        address=urlsplit(link)
        match=PARSER_RE.match(address.path)
        if address.scheme!="https" or not match:
            continue
        artist,post_id=match.groups()
        if post_id in seen:continue
        seen.add(post_id)
        results.append({"id":post_id,"artist":artist,"group":group,"tag":hashtag,
                        "text":text[:2000]})
    return results[:MAX_PER_TAG]

def collect(*,session=None,resolver=None,queue=QUEUE):
    session=session or requests.Session()
    resolver=resolver or resolve_media
    catalog=read(queue)
    if not isinstance(catalog,dict):
        raise ValueError("Unexpected fanart queue")
    counts={}
    added=0
    for group,tag in TAGS.items():
        raw_posts=[]
        sources=[]
        for hostname in HOSTS:
            url="https://"+hostname+"/search/rss?f=tweets&q="+quote("#"+tag)
            try:
                resp=session.get(url,timeout=(3,6),headers={"User-Agent":"abyss-bot/1.0 (public fanart review)"})
                resp.raise_for_status()
                found=candidates_from_rss(resp.content,group,tag)
                raw_posts.extend(found)
                sources.append({"host":hostname,"items":len(found)})
            except (requests.RequestException,ET.ParseError,ValueError,OSError) as exc:
                logging.warning("FANART_RSS_SOURCE_UNAVAILABLE group=%s host=%s reason=%s",group,hostname,type(exc).__name__)
        unique={}
        for item in raw_posts:
            unique.setdefault(item["id"],item)
        accepted=0
        for item in sorted(unique.values(),key=lambda x:int(x["id"]),reverse=True):
            if added>=MAX_PER_RUN:break
            key=group+":"+item["id"]
            if key in catalog:continue
            # The same verified X metadata service used for repost photos.
            try:
                images,_,_,verified=resolver({"id":item["id"],"images":[]},item["artist"])
            except (requests.RequestException,ValueError,TypeError) as exc:
                logging.warning("FANART_RSS_MEDIA_UNAVAILABLE group=%s reason=%s",group,type(exc).__name__)
                continue
            safe_images=[]
            for url in images[:4]:
                parts=urlsplit(url)
                if parts.scheme=="https" and parts.hostname=="pbs.twimg.com" and parts.path.startswith("/media/"):
                    safe_images.append(url)
            if not verified or not safe_images:
                continue
            decision=classify(item["text"])
            catalog[key]={"group":group,"hashtag":"#"+tag,"post_id":item["id"],
                          "post_url":f"https://x.com/{item['artist']}/status/{item['id']}",
                          "artist":"@"+item["artist"],"image_urls":safe_images,
                          "decision":decision,
                          "review_status":"blocked" if decision=="blocked" else "pending",
                          "discovered_at":datetime.now(timezone.utc).isoformat(),
                          "text":item["text"],"discovery_source":"public_hashtag_rss"}
            added+=1
            accepted+=1
        counts[group]={"found":len(unique),"added_for_review":accepted,"sources":sources}
    if added:write(queue,catalog)
    return {"status":"ok","added_for_review":added,"groups":counts,
            "publicly_approved":0,"queue_total":len(catalog)}

if __name__=="__main__":
    print("FANART_RSS_REVIEW "+json.dumps(collect(),ensure_ascii=False),flush=True)
