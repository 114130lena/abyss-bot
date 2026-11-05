"""CHZZK Hanayo Hiyori clips: read-only public metadata collector.

This collector never downloads video files, accesses cookies, or posts to Discord.
CHZZK service endpoints are unofficial and may change without notice.
"""
import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

CHANNEL="49d2c3cf8f0b2843d934ec15e82bb54c"
KST=ZoneInfo("Asia/Seoul")
ENDPOINT=f"https://api.chzzk.naver.com/service/v1/channels/{CHANNEL}/clips"
ROOT=Path(__file__).resolve().parent/"creator_media"
INDEX=ROOT/"chzzk_hiyoriclip_posts.json"
STATUS=ROOT/"chzzk_hiyoriclip_status.json"
HEADERS={"User-Agent":"Mozilla/5.0 (compatible; abyss-bot clip metadata/1.0)",
         "Accept":"application/json"}
UID=re.compile(r"^[A-Za-z0-9_-]{6,40}$")


def parse_created(value):
    try:
        stamp=datetime.strptime(str(value),"%Y-%m-%d %H:%M:%S")
        return stamp.replace(tzinfo=KST).astimezone(timezone.utc)
    except (ValueError,TypeError):
        return None


def clip_row(raw, *, now):
    if not isinstance(raw,dict):
        return None
    uid=str(raw.get("clipUID") or "")
    if not UID.fullmatch(uid) or raw.get("ownerChannelId")!=CHANNEL:
        return None
    when=parse_created(raw.get("createdDate"))
    if when is None or when>now+timedelta(minutes=2):
        return None
    if raw.get("adult") is True or raw.get("blindType") not in (None,"","NONE"):
        return None
    if raw.get("privateUserBlock"):
        return None
    views=raw.get("readCount")
    if type(views) is not int or views<0:
        return None
    duration=raw.get("duration")
    if type(duration) not in (int,float) or isinstance(duration,bool) or duration<=0:
        return None
    video=str(raw.get("videoId") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,60}",video):
        return None
    return {
        "id":uid,"platform":"chzzk","channel_id":CHANNEL,
        "url":f"https://chzzk.naver.com/clips/{uid}",
        "title":str(raw.get("clipTitle") or "").strip()[:300],
        "created_at":when.isoformat(),
        "views":views,"duration_seconds":duration,
        "video_id":video,
        "thumbnail_url":str(raw.get("thumbnailImageUrl") or "")[:1200],
        "clip_category":str(raw.get("clipCategory") or "")[:100],
        "last_checked":now.isoformat(),
    }


def _write(path,payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def read(path):
    try:
        obj=json.loads(path.read_text("utf-8"))
        return obj if isinstance(obj,dict) else {}
    except (ValueError,OSError):
        return {}


def fetch_recent(*,session=None,now=None,max_pages=12,hours=24):
    now=(now or datetime.now(KST)).astimezone(timezone.utc)
    if max_pages<1 or hours<1:
        raise ValueError("Invalid collection bounds")
    session=session or requests.Session()
    seen=set()
    collected={}
    cursor={}
    cutoff=now-timedelta(hours=hours)
    completed=False
    pages=0
    for index in range(max_pages):
        params={"filterType":"ALL","orderType":"RECENT","size":30,**cursor}
        response=session.get(ENDPOINT,params=params,headers=HEADERS,timeout=(4,20))
        response.raise_for_status()
        body=response.json()
        if not isinstance(body,dict) or body.get("code")!=200:
            raise ValueError("CHZZK API returned unexpected status")
        payload=body.get("content")
        if not isinstance(payload,dict) or not isinstance(payload.get("data"),list):
            raise ValueError("CHZZK clip list data missing")
        clips=payload["data"]
        pages+=1
        dates=[]
        for item in clips:
            when=parse_created(item.get("createdDate")) if isinstance(item,dict) else None
            if when is not None:
                dates.append(when)
            row=clip_row(item,now=now)
            if row and cutoff<=when<=now:
                collected[row["id"]]=row
        next_cursor=(payload.get("page") or {}).get("next")
        if not next_cursor or not clips:
            completed=True
            break
        if dates and max(dates)<cutoff:
            completed=True
            break
        if not isinstance(next_cursor,dict) or not next_cursor.get("clipUID"):
            raise ValueError("Unexpected CHZZK pagination cursor")
        key=(next_cursor.get("clipUID"),next_cursor.get("readCount"))
        if key in seen:
            raise ValueError("Repeated CHZZK clip cursor")
        seen.add(key)
        cursor={"clipUID":next_cursor["clipUID"]}
        if next_cursor.get("readCount") is not None:
            cursor["readCount"]=next_cursor["readCount"]
    return collected,{"pages":pages,"complete_24h_scan":completed,
                      "recent_video_count":len(collected),
                      "window_start":cutoff.isoformat(),"window_end":now.isoformat()}


def collect(*,session=None,now=None):
    now=(now or datetime.now(KST)).astimezone(timezone.utc)
    data,report=fetch_recent(session=session,now=now)
    previous=read(INDEX).get("clips",{})
    if not isinstance(previous,dict):
        previous={}
    # Keep historical metadata for 7 days so already-seen clip URLs are not lost.
    minimum=now-timedelta(days=7)
    catalog={key:value for key,value in previous.items()
             if isinstance(value,dict) and (created:=parse_iso(value.get("created_at")))
             and created>=minimum}
    count_new=sum(uid not in catalog for uid in data)
    catalog.update(data)
    _write(INDEX,{"version":1,"clips":catalog})
    status={"checked_at":now.isoformat(),"platform":"chzzk","channel_id":CHANNEL,
            "new_clips":count_new,"stored_7_days":len(catalog),**report,
            "safe_for_daily_selection":report["complete_24h_scan"],
            "discord_publishing_enabled":False}
    _write(STATUS,status)
    return status


def parse_iso(value):
    try:
        date=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return date.astimezone(timezone.utc) if date.tzinfo else None
    except (ValueError,TypeError):
        return None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--show",action="store_true")
    opts=parser.parse_args()
    if opts.show:
        print("CHZZK_HIYORI_COLLECT "+json.dumps(read(STATUS),ensure_ascii=False))
    else:
        print("CHZZK_HIYORI_COLLECT "+json.dumps(collect(),ensure_ascii=False))

if __name__=="__main__":
    main()
