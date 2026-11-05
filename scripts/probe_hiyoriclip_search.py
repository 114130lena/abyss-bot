"""Read-only public search diagnostic, never posts or stores Discord messages."""
import json
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import requests

URL = "https://api.fxtwitter.com/2/search"
HASHTAG = "#hiyoriclip_"
QUERIES = [HASHTAG + " filter:videos", HASHTAG, "puppies", "HanayoHiyori"]

def metric(v):
    return v if type(v) is int and v >= 0 else None

def extract(page):
    raw=page.get("results",[])
    if not isinstance(raw,list):
        return []
    result=[]
    now=datetime.now(timezone.utc)
    for e in raw:
        if not isinstance(e,dict):
            continue
        body=str(e.get("text",""))
        if not re.search(r"(?i)(?<!\w)#hiyoriclip_(?!\w)",body):
            continue
        media=e.get("media") if isinstance(e.get("media"),dict) else {}
        videos=media.get("videos") if isinstance(media.get("videos"),list) else []
        if not videos:
            continue
        try:
            when=datetime.fromtimestamp(float(e.get("created_timestamp")),timezone.utc)
        except (TypeError,ValueError,OverflowError,OSError):
            continue
        author=e.get("author") if isinstance(e.get("author"),dict) else {}
        result.append({"id":str(e.get("id","")),"handle":author.get("screen_name"),
                       "age_hours":round((now-when).total_seconds()/3600,2),
                       "likes":metric(e.get("likes")),"reposts":metric(e.get("reposts")),
                       "views":metric(e.get("views")),"videos":len(videos)})
    return result

def main():
    all_results={}
    session=requests.Session()
    for query in QUERIES:
        try:
            r=session.get(URL,params={"q":query,"feed":"latest","count":30},
                timeout=18,headers={"User-Agent":"abyss-bot/1.0 daily-clip-probe"})
            content_type=r.headers.get("content-type","")
            result={"http":r.status_code,"content_type":content_type[:60]}
            if r.status_code==200 and "json" in content_type:
                page=r.json()
                result.update({"code":page.get("code"),
                  "results_count":len(page.get("results",[])) if isinstance(page.get("results"),list) else -1,
                  "has_cursor":bool(page.get("cursor",{}).get("bottom")) if isinstance(page.get("cursor"),dict) else False,
                  "message":str(page.get("message",""))[:90]})
                rows=extract(page)
                result["valid_hiyoriclip_video_count"]=len(rows)
                result["within_24h_count"]=sum(0<=p["age_hours"]<=24 for p in rows)
                result["sample"]=rows[:5]
                result["metrics_available"]={m:sum(row[m] is not None for row in rows)
                    for m in ("likes","reposts","views")}
            else:
                result["error_body_excerpt"]=r.text[:100]
            all_results[query]=result
        except (requests.RequestException,ValueError) as e:
            all_results[query]={"error":type(e).__name__,"message":str(e)[:110]}
    search_endpoints = [
        "https://nitter.meowing.monster/search/rss",
        "https://nitter.mie.foundation/search/rss",
        "https://nitter.mizuki.st/search/rss",
    ]
    for endpoint in search_endpoints:
        host=urlparse(endpoint).hostname
        try:
            r=session.get(endpoint,params={"f":"tweets","q":HASHTAG},
                          timeout=8,headers={"User-Agent":"abyss-bot/1.0 daily-clip-probe"})
            all_results["rss:"+str(host)]={"http":r.status_code,
                "is_rss":r.text.lstrip().startswith('<?xml') or '<rss' in r.text[:500],
                "bytes":len(r.content)}
        except requests.RequestException as e:
            all_results["rss:"+str(host)]={"error":type(e).__name__}
    print("HIYORICLIP_DIAGNOSTIC "+json.dumps(all_results,ensure_ascii=False))
if __name__=="__main__":
    main()
