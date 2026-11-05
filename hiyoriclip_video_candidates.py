"""Produce a private, deterministic video-only candidate index from verified X posts.

Runs after collection under the same lock. This is NOT an exhaustive hashtag
search and never sends Discord messages or chooses a winner.
"""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from hiyoriclip_video_screen import inspect, _mp4

BASE=Path(os.getenv("BOT_DATA_DIR","/home/ubuntu/chzzk-bot"))/"creator_media"
SOURCE=BASE/"hiyoriclip_posts.json"
DEST=BASE/"hiyoriclip_video_candidates.json"

def fingerprint(post):
    """Match identical X media IDs even if re-shared by a different author."""
    ids=[str(x).strip() for x in (post.get("video_ids") or []) if str(x).strip()]
    if ids:
        return "id:"+ids[0]
    for url in post.get("video_urls") or []:
        if _mp4(url):
            return "mp4:"+urlsplit(url).path
    return "post:"+str(post.get("id"))

def shortlist(index, *, now=None, hours=24):
    posts=index.get("posts",{})
    if not isinstance(posts,dict):
        raise ValueError("Invalid source catalog")
    screening=inspect(posts,hours=hours,now=now,limit=max(1,len(posts)+1))
    unique={}
    duplicate_videos=0
    for candidate in screening["sample_recent"]:
        raw=posts.get(candidate["id"],{})
        key=fingerprint(raw)
        if key in unique:
            duplicate_videos+=1
            # Keep the entry with the strongest engagement if identical media.
            prev=unique[key]
            strength=lambda p:(p.get("likes") or 0,p.get("reposts") or 0,p.get("views") or 0)
            if strength(candidate)<=strength(prev):
                continue
        playable=[url for url in raw.get("video_urls",[]) if _mp4(url)]
        unique[key]={**candidate,"video_key":key,"mp4_url":playable[0] if playable else None}
    records=sorted(unique.values(),key=lambda c:c["created_at"],reverse=True)
    return {"version":1,"checked_at":screening["checked_at"],"window_hours":hours,
            "global_hashtag_search_confirmed":False,
            "scope":"verified profiles / RSS references only; not full X hashtag",
            "video_posts_before_dedup":screening["video_posts"],
            "duplicate_video_reposts":duplicate_videos,
            "unique_videos":len(records),
            "with_direct_mp4":sum(bool(x["mp4_url"]) for x in records),
            "with_all_metrics":sum(all(x[k] is not None for k in ("views","likes","reposts")) for x in records),
            "candidates":records}

def save_atomic(path,data):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(".tmp")
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(path)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--index",default=str(SOURCE))
    p.add_argument("--output",default=str(DEST))
    p.add_argument("--hours",type=int,default=24)
    p.add_argument("--dry-run",action="store_true")
    args=p.parse_args()
    data=json.loads(Path(args.index).read_text("utf-8"))
    results=shortlist(data,hours=args.hours)
    if not args.dry_run:
        save_atomic(args.output,results)
    print("HIYORICLIP_CANDIDATES "+json.dumps({
        key:results[key] for key in ("window_hours","video_posts_before_dedup","duplicate_video_reposts",
                                   "unique_videos","with_direct_mp4","with_all_metrics")},
        ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
