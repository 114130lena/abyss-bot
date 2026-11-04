"""Periodic official-X-API hashtag discovery, with safe classification.

Uses X recent-search API only; without X_BEARER_TOKEN it is a no-op.
Stores metadata for review, never downloads or republishes unlicensed art.
"""
import json
import logging
import os
import re
from pathlib import Path
import requests
from fanart_character_guard import mixed_character_tags
from datetime import datetime, timezone

BASE=Path(os.getenv("BOT_DATA_DIR","/home/ubuntu/chzzk-bot"))/"creator_media"
QUEUE=BASE/"fanart_discovery.json"
STATE=BASE/"fanart_discovery_state.json"
TAGS={"hanayo":"hiyoriart_","sakihane":"huyart"}
DENY=(r"무단\s*(?:전재|사용|배포|재배포)",r"(?:사용|배포|전재|재게시)\s*금지",
      r"転載禁止",r"無断(?:転載|使用)",r"再配布禁止",r"二次配布禁止",
      r"do\s*not\s*(?:repost|redistribute|use)",r"no\s*(?:repost|redistribution|reupload)",
      r"禁止轉載",r"禁止转载")
CREDIT=(r"출처.{0,12}(?:표기|남겨)",r"クレジット.{0,15}(?:記載|表記)",
        r"credit.{0,20}(?:required|please|source)",)
ALLOW=(r"(?:출처|크레딧).{0,20}(?:사용\s*가능|사용\s*허용|이용\s*가능)",
       r"(?:사용|이용|재게시).{0,12}(?:허용|가능)",r"(?:repost|reuse).{0,18}(?:allowed|ok|permitted)")
MAX_SEEN=20000

def read(path):
    try:
        data=json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data,dict) else {}
    except (ValueError,OSError): return {}

def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(path)

def classify(text,profile=""):
    content=(str(text)+"\n"+str(profile))[:8000]
    if any(re.search(p,content,re.I) for p in DENY):
        return "blocked"
    if any(re.search(p,content,re.I) for p in ALLOW):
        return "permission_candidate"
    if any(re.search(p,content,re.I) for p in CREDIT):
        return "credit_required_review"
    return "unknown"

def discover(session=None):
    token=os.getenv("X_BEARER_TOKEN","").strip()
    if not token:
        logging.info("Fanart discovery disabled: X_BEARER_TOKEN not configured")
        return {"status":"disabled","added":0}
    session=session or requests.Session()
    catalog=read(QUEUE)
    state=read(STATE)
    added=0
    for group,tag in TAGS.items():
        old=state.get(group,{})
        since=old.get("since_id")
        params={"query":f"#{tag} has:images -is:retweet",
                "max_results":100,"expansions":"attachments.media_keys,author_id",
                "tweet.fields":"author_id,created_at,entities,attachments,possibly_sensitive",
                "user.fields":"username","media.fields":"media_key,type,url,preview_image_url"}
        if since and str(since).isdigit():params["since_id"]=since
        max_id=since
        page_token=None
        for _ in range(3):
            if page_token:params["next_token"]=page_token
            response=session.get("https://api.x.com/2/tweets/search/recent",params=params,
                 headers={"Authorization":"Bearer "+token},timeout=18)
            if response.status_code in (401,403,429):
                logging.warning("X API restricted for %s HTTP %d",group,response.status_code)
                return {"status":"api_unavailable","added":added,"http":response.status_code}
            response.raise_for_status()
            data=response.json()
            users={u["id"]:u.get("username","") for u in data.get("includes",{}).get("users",[])}
            media={m["media_key"]:m for m in data.get("includes",{}).get("media",[])}
            for post in data.get("data",[]):
                post_id=str(post.get("id",""))
                if not post_id.isdigit():continue
                hashtag_values=[t.get("tag","").casefold() for t in post.get("entities",{}).get("hashtags",[])]
                if tag.casefold() not in hashtag_values:continue
                if mixed_character_tags(group, post.get("text", ""), hashtag_values):
                    continue  # Mixed characters: not eligible for a single-creator banner.
                if not max_id or int(post_id)>int(max_id):max_id=post_id
                image_keys=[k for k in post.get("attachments",{}).get("media_keys",[])
                            if media.get(k,{}).get("type")=="photo"]
                if not image_keys:continue
                artist=users.get(post.get("author_id"),"")
                if not artist:continue
                key=group+":"+post_id
                if key in catalog:continue
                decision=classify(post.get("text",""))
                catalog[key]={"group":group,"hashtag":"#"+tag,"post_id":post_id,
                    "post_url":f"https://x.com/{artist}/status/{post_id}",
                    "artist":"@"+artist,"image_urls":[media[k]["url"] for k in image_keys if media[k].get("url")],
                    "decision":decision,"all_hashtags":hashtag_values,
                    "review_status":"blocked" if decision=="blocked" else "pending",
                    "discovered_at":datetime.now(timezone.utc).isoformat(),"text":post.get("text","")[:2000]}
                added+=1
            page_token=data.get("meta",{}).get("next_token")
            if not page_token:break
        if max_id:
            state[group]={"since_id":max_id,"checked_at":datetime.now(timezone.utc).isoformat()}
        write(QUEUE,catalog)
        write(STATE,state)
    if len(catalog)>MAX_SEEN:
        # Preserve blocked and old history; never truncate without a retention policy.
        logging.warning("Fanart candidate queue exceeded %s entries",MAX_SEEN)
    return {"status":"ok","added":added}

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(discover(),ensure_ascii=False))
