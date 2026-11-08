"""Discover fan-art candidates from FxEmbed's publicly documented account RSS.

This is a *profile feed*, NOT a global hashtag search. Does not auto-repost.
Preserves provenance and routes matching posts to the existing review queue.
"""
import json,logging,re,os
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
import requests
from fanart_discovery import BASE,QUEUE,read,write,classify

ACCOUNTS={"hanayo":("HanayoHiyori","hiyoriart_"),"sakihane":("SakihaneHuya","huyart")}
MEDIA_HOSTS={"pbs.twimg.com"}
POST=re.compile(r"https?://(?:x\.com|twitter\.com|fxtwitter\.com)/([A-Za-z0-9_]{1,15})/status/(\d+)",re.I)
HASHTAG=re.compile(r"(?<![\w])#([A-Za-z0-9_]+)",re.I)

class Images(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.urls=[]
    def handle_starttag(self,tag,attrs):
        if tag not in ("img","source"):return
        data=dict(attrs)
        for key in ("src","data-src","data-original"):
            self.add(data.get(key))
    def add(self,url):
        if not isinstance(url,str):return
        if url.startswith("//"):url="https:"+url
        p=urlparse(url)
        if p.scheme=="https" and p.hostname in MEDIA_HOSTS and "/media/" in p.path and url not in self.urls:
            self.urls.append(url)

def fetch_group(group,session=None):
    account,tag=ACCOUNTS[group]
    session=session or requests.Session()
    url=f"https://fxtwitter.com/{account}/feed.xml"
    response=session.get(url,params={"count":100,"safe":1},timeout=20,
                         headers={"User-Agent":"abyss-bot/1.0 (public RSS)"})
    response.raise_for_status()
    if len(response.content)>5_000_000:raise ValueError("Oversized RSS feed")
    root=ET.fromstring(response.content)
    if root.tag!="rss":raise ValueError("Not RSS")
    channel=root.find("channel")
    if channel is None:raise ValueError("No RSS channel")
    queue=read(QUEUE);added=0;seen=0
    for item in channel.findall("item"):
        raw=item.findtext("link") or item.findtext("guid") or ""
        match=POST.search(raw)
        if not match:continue
        author,post_id=match.groups()
        description=item.findtext("description") or ""
        title=item.findtext("title") or ""
        combined=title+" "+re.sub("<[^>]*>"," ",description)
        if tag.casefold() not in [t.casefold() for t in HASHTAG.findall(combined)]:continue
        images=Images()
        images.feed(description)
        for child in item:
            if child.tag.rsplit("}",1)[-1] in ("enclosure","content","thumbnail"):
                images.add(child.attrib.get("url"))
        if not images.urls:continue
        key=group+":"+post_id
        seen+=1
        if key in queue:continue
        decision=classify(combined)
        queue[key]={"group":group,"hashtag":"#"+tag,"post_id":post_id,
            "post_url":f"https://x.com/{author}/status/{post_id}",
            "artist":"@"+author,"image_urls":images.urls[:4],
            "decision":decision,"review_status":"blocked" if decision=="blocked" else "pending",
            "source":"fxtwitter_profile_feed","discovered_at":datetime.now(timezone.utc).isoformat(),"text":combined[:2000]}
        added+=1
    if added:
        write(QUEUE,queue)
    return {"account":account,"matched":seen,"added":added}

def run():
    results={}
    for group in ACCOUNTS:
        try:results[group]=fetch_group(group)
        except (requests.RequestException,ET.ParseError,ValueError,OSError) as exc:
            logging.warning("FxEmbed %s feed unavailable: %s",group,exc)
            results[group]={"error":type(exc).__name__}
    print(json.dumps(results,ensure_ascii=False))
    return results

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO)
    run()
