"""Collect #hiyoriclip_ posts into a local JSON index. No Discord publishing.

Providers:
1. Official X recent search when an authorized bearer token exists.
2. Public FxTwitter *account* RSS, for opt-in known clip creator handles.
3. Explicit post URLs from a local text file, verified through FxTwitter status API.

Profile feeds are NOT global hashtag search. Source status and completeness
must not be represented as exhaustive when the official search is unavailable.
"""
import argparse
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import requests
from hiyoriclip_discovery import candidates_from_rss, new_accounts

ROOT=Path(os.getenv("BOT_DATA_DIR","/home/ubuntu/chzzk-bot"))
DEST=ROOT/"creator_media"
INDEX=DEST/"hiyoriclip_posts.json"
STATUS=DEST/"hiyoriclip_collect_status.json"
ACCOUNTS=DEST/"hiyoriclip_accounts.txt"
URLS=DEST/"hiyoriclip_post_urls.txt"
DISCOVERED=DEST/"hiyoriclip_discovered_accounts.txt"
TAG=re.compile(r"(?i)(?<!\w)#hiyoriclip_(?!\w)")
POST=re.compile(r"^/(?:([A-Za-z0-9_]{1,15}))/status/([0-9]{10,25})$")
MAX_BYTES=4_000_000
MAX_POSTS=10000


class RSSBody(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.words=[]
    def handle_data(self,data):
        self.words.append(data)
    def handle_starttag(self,tag,attrs):
        if tag in ("br","p","div"):
            self.words.append(" ")


def parse_url(value):
    parsed=urlsplit(str(value or "").strip())
    if parsed.scheme!="https" or parsed.hostname not in (
            "x.com","twitter.com","fxtwitter.com","fixupx.com") or parsed.username or parsed.password:
        return None
    found=POST.fullmatch(parsed.path.rstrip("/"))
    return found.groups() if found else None


def handles():
    if not ACCOUNTS.is_file():
        return []
    results=[]
    for line in ACCOUNTS.read_text("utf-8").splitlines():
        val=line.split("#",1)[0].strip().lstrip("@")
        if re.fullmatch(r"[A-Za-z0-9_]{1,15}",val) and val.casefold() not in [x.casefold() for x in results]:
            results.append(val)
    if DISCOVERED.is_file():
        for line in DISCOVERED.read_text("utf-8").splitlines():
            val=line.split("#",1)[0].strip().lstrip("@")
            if re.fullmatch(r"[A-Za-z0-9_]{1,15}",val) and val.casefold() not in [x.casefold() for x in results]:
                results.append(val)
    return results[:70]


def supplied_posts():
    if not URLS.is_file():
        return []
    valid=[]
    for line in URLS.read_text("utf-8").splitlines():
        parsed=parse_url(line.strip())
        if parsed and parsed not in valid:
            valid.append(parsed)
    return valid[:500]


def clean_count(value):
    return int(value) if isinstance(value,int) and not isinstance(value,bool) and value>=0 else None


def parse_time(value):
    try:
        if isinstance(value,(float,int)) and not isinstance(value,bool):
            return datetime.fromtimestamp(value,timezone.utc)
        if isinstance(value,str) and value:
            return datetime.fromisoformat(value.replace("Z","+00:00")).astimezone(timezone.utc)
    except (ValueError,OverflowError,TypeError,OSError):
        pass
    return None


def fx_post(session,handle,post_id):
    response=session.get(f"https://api.fxtwitter.com/{handle}/status/{post_id}",
           timeout=(3,12),headers={"User-Agent":"abyss-bot/1.0 hiyoriclip-collection"})
    response.raise_for_status()
    if len(response.content)>MAX_BYTES:
        raise ValueError("Oversized status JSON")
    payload=response.json()
    if not isinstance(payload,dict):
        raise ValueError("Invalid JSON shape")
    data=payload.get("tweet")
    if not isinstance(data,dict):
        raise ValueError("Missing X post")
    return data


def normalize(tweet,handle,post_id,source):
    if not isinstance(tweet,dict):
        return None
    if str(tweet.get("id",""))!=str(post_id):
        return None
    text=str(tweet.get("text") or "")
    if not TAG.search(text):
        return None
    if tweet.get("retweeted_by") or tweet.get("reposted_by"):
        return None
    author=tweet.get("author") if isinstance(tweet.get("author"),dict) else {}
    name=str(author.get("screen_name") or handle)
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}",name):
        return None
    # Verify the post author in returned metadata, not the queried URL alone.
    if name.casefold()!=str(handle).casefold():
        return None
    created=parse_time(tweet.get("created_timestamp") or tweet.get("created_at"))
    if created is None:
        return None
    media=tweet.get("media") if isinstance(tweet.get("media"),dict) else {}
    videos=media.get("videos") or media.get("animated_gifs") or media.get("gifs") or []
    if not isinstance(videos,list) or not videos:
        return None
    playable=[]
    video_ids=[]
    for v in videos:
        if not isinstance(v,dict):
            continue
        video_ids.append(str(v.get("id") or ""))
        candidate=str(v.get("url") or "")
        parts=urlsplit(candidate)
        if parts.scheme=="https" and parts.hostname=="video.twimg.com" and parts.path.lower().endswith(".mp4"):
            playable.append(candidate)
    if not video_ids and not playable:
        return None
    now=datetime.now(timezone.utc)
    return {"id":str(post_id),"author":"@"+name,
      "url":f"https://x.com/{name}/status/{post_id}",
      "text":text[:5000],"created_at":created.isoformat(),
      "video_ids":video_ids[:4],"video_urls":playable[:4],
      "views":clean_count(tweet.get("views")),
      "likes":clean_count(tweet.get("likes")),
      "reposts":clean_count(tweet.get("retweets")) if clean_count(tweet.get("reposts")) is None else clean_count(tweet.get("reposts")),
      "last_seen":now.isoformat(),"discovered_via":[source]}


def _read(path):
    try:
        result=json.loads(path.read_text("utf-8"))
        return result if isinstance(result,dict) else {}
    except (ValueError,OSError,TypeError):
        return {}


def _write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(".tmp")
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(path)


def merged(previous,incoming):
    if not previous:
        return incoming
    result={**previous,**incoming}
    result["first_seen"]=previous.get("first_seen",previous.get("last_seen",incoming["last_seen"]))
    result["discovered_via"]=sorted(set(previous.get("discovered_via",[])+incoming["discovered_via"]))
    for field in ("views","likes","reposts"):
        if incoming.get(field) is None:
            result[field]=previous.get(field)
    return result


def account_feed(session,handle):
    response=session.get(f"https://fxtwitter.com/{handle}/feed.xml",
                         params={"count":100},timeout=(3,15),
                         headers={"User-Agent":"abyss-bot/1.0 hiyoriclip-rss"})
    response.raise_for_status()
    if len(response.content)>MAX_BYTES:
        raise ValueError("Oversized RSS feed")
    root=ET.fromstring(response.content)
    if root.tag!="rss":
        raise ValueError("Not RSS")
    channel=root.find("channel")
    if channel is None:
        raise ValueError("No RSS channel")
    results=[]
    for item in channel.findall("item"):
        source=parse_url(item.findtext("link") or item.findtext("guid"))
        if not source or source[0].casefold()!=handle.casefold():
            continue
        parser=RSSBody()
        parser.feed(item.findtext("description") or "")
        body=(item.findtext("title") or "")+" "+" ".join(parser.words)
        if TAG.search(body):
            results.append(source)
    # A quoted/reposted original from another account can reveal a new creator.
    # Do not trust RSS alone: each candidate is validated by fx_post+normalize.
    references=candidates_from_rss(response.content.decode("utf-8","replace"),handle,TAG)
    for author,post_id in references:
        if (author,post_id) not in results:
            results.append((author,post_id))
    return results[:160]


def official_search(session,token):
    results=[]
    params={"query":"#hiyoriclip_ has:videos -is:retweet",
            "max_results":100,
            "tweet.fields":"author_id,created_at,entities,attachments,public_metrics",
            "expansions":"author_id,attachments.media_keys",
            "user.fields":"username",
            "media.fields":"type,media_key,preview_image_url",
            }
    for _ in range(10):
        res=session.get("https://api.x.com/2/tweets/search/recent",
              headers={"Authorization":"Bearer "+token},params=params,timeout=(3,15))
        res.raise_for_status()
        page=res.json()
        if not isinstance(page,dict):
            raise ValueError("Malformed official X search page")
        authors={str(u.get("id")):u.get("username") for u in page.get("includes",{}).get("users",[])}
        for item in page.get("data",[]):
            if not isinstance(item,dict) or not TAG.search(str(item.get("text",""))):
                continue
            user=authors.get(str(item.get("author_id")))
            post_id=str(item.get("id",""))
            if user and post_id.isdigit():
                results.append((user,post_id))
        cursor=page.get("meta",{}).get("next_token")
        if not cursor:
            break
        params["next_token"]=cursor
    return results


def collect(*, session=None, now=None):
    now=now or datetime.now(timezone.utc)
    session=session or requests.Session()
    previous=_read(INDEX)
    catalog=dict(previous.get("posts",{}))
    targets={}
    sources={}
    token=os.getenv("X_BEARER_TOKEN","").strip()
    if token:
        try:
            found=official_search(session,token)
            sources["official_recent_search"]={"status":"ok","discovered":len(found)}
            for handle,post_id in found:
                targets.setdefault(post_id,(handle,post_id,"official_recent_search"))
        except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
            sources["official_recent_search"]={"status":"error","reason":type(exc).__name__}
    else:
        sources["official_recent_search"]={"status":"disabled_no_token"}
    account_list=handles()
    for handle in account_list:
        try:
            found=account_feed(session,handle)
            sources["profile:"+handle]={"status":"ok","discovered":len(found)}
            for author,post_id in found:
                targets.setdefault(post_id,(author,post_id,"profile:"+handle))
        except (requests.RequestException,ET.ParseError,ValueError,OSError) as exc:
            sources["profile:"+handle]={"status":"error","reason":type(exc).__name__}
    explicit=supplied_posts()
    for handle,post_id in explicit:
        targets.setdefault(post_id,(handle,post_id,"supplied_url"))
    sources["supplied_urls"]={"status":"ok","count":len(explicit)}
    new=0
    enriched=0
    errors=0
    for post_id,(handle,pid,source) in targets.items():
        try:
            item=normalize(fx_post(session,handle,pid),handle,pid,source)
            if item is None:
                continue
            if post_id in catalog:
                catalog[post_id]=merged(catalog[post_id],item)
                enriched+=1
            else:
                item["first_seen"]=item["last_seen"]
                catalog[post_id]=item
                new+=1
        except (requests.RequestException,ValueError,TypeError,KeyError,OverflowError) as exc:
            errors+=1
    # Discover additional creator accounts only from original verified video clips.
    # Never promote a feed reference or a failed lookup alone.
    known=handles()
    proposed=new_accounts(catalog,known,min_clips=1,limit=30)
    existing_discovered=[]
    if DISCOVERED.is_file():
        existing_discovered=[v.strip() for v in DISCOVERED.read_text("utf-8").splitlines()
                             if re.fullmatch(r"[A-Za-z0-9_]{1,15}",v.strip())]
    seen_names={x.casefold() for x in existing_discovered}
    additions=[]
    for name in proposed:
        if name.casefold() not in seen_names and len(existing_discovered)+len(additions)<30:
            additions.append(name)
            seen_names.add(name.casefold())
    if additions:
        _write_accounts=DISCOVERED.with_suffix(".tmp")
        _write_accounts.parent.mkdir(parents=True,exist_ok=True)
        _write_accounts.write_text("\\n".join(existing_discovered+additions)+"\\n",encoding="utf-8")
        _write_accounts.chmod(0o600)
        _write_accounts.replace(DISCOVERED)
    sources["verified_account_discovery"]={"new_accounts":additions,
                                         "discovered_account_total":len(existing_discovered)+len(additions)}
    # Never delete historical clips/selection records merely due to source outage.
    if len(catalog)>MAX_POSTS:
        raise RuntimeError("Clip index reached its safety limit; manual retention review required")
    _write(INDEX,{"version":1,"posts":catalog})
    provider_healthy=bool(sources.get("official_recent_search",{}).get("status")=="ok")
    report={"checked_at":now.isoformat(),"hashtag":"#hiyoriclip_",
            "global_hashtag_search_confirmed":provider_healthy,
            "status":"ok" if provider_healthy else "partial_profile_or_manual_only",
            "sources":sources,"new":new,"updated":enriched,
            "total":len(catalog),"lookup_errors":errors,
            "within_24h":sum(
                (date:=parse_time(e.get("created_at"))) is not None
                and now-timedelta(hours=24)<=date<=now for e in catalog.values())}
    _write(STATUS,report)
    return report


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--show",action="store_true",help="Read existing metadata without requests")
    a=p.parse_args()
    if a.show:
        print(json.dumps({"status":_read(STATUS),"posts":len(_read(INDEX).get("posts",{}))},ensure_ascii=False))
    else:
        print(json.dumps(collect(),ensure_ascii=False))
