"""Read-only ranking preview for collected Hiyoriclip posts, no Discord publishing."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from daily_hiyoriclip_selector import rank_clips, DAILY_RECOMMENDATION_HOUR_KST, DAILY_RECOMMENDATION_MINUTE_KST
from hiyoriclip_video_screen import inspect, _date

KST=ZoneInfo("Asia/Seoul")

def transform(row):
    author=str(row.get("author") or "").lstrip("@")
    videos=[{"id":str(v)} for v in (row.get("video_ids") or []) if str(v)]
    if not videos:
        videos=[{"url":url} for url in (row.get("video_urls") or [])]
    when=_date(row.get("created_at"))
    return {
        "id":str(row.get("id","")),
        "text":row.get("text",""),
        "author":{"screen_name":author},
        "created_timestamp":when.timestamp() if when else None,
        "media":{"videos":videos},
        "views":row.get("views"),"likes":row.get("likes"),
        "reposts":row.get("reposts"),
    }

def preview(posts, *, now=None, limit=10, used_ids=()):
    now=now or datetime.now(KST)
    audit=inspect(posts,now=now,limit=1)
    ranked=rank_clips([transform(x) for x in posts.values()],
                      now=now,used_ids=used_ids)
    return {"checked_at":now.isoformat(),"window_hours":24,
            "daily_recommendation_time_kst":f"{DAILY_RECOMMENDATION_HOUR_KST:02d}:{DAILY_RECOMMENDATION_MINUTE_KST:02d}",
            "publication_enabled":False,
            "candidate_total":audit["video_posts"],
            "ranked_count":len(ranked),
            "shortlist":ranked[:limit],
            "published":False,"coverage":"partial_known_accounts_only"}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--index",default=str(Path(__file__).resolve().parent/"creator_media"/"hiyoriclip_posts.json"))
    parser.add_argument("--limit",type=int,default=10)
    args=parser.parse_args()
    posts=json.loads(Path(args.index).read_text("utf-8")).get("posts",{})
    if not isinstance(posts,dict):
        raise ValueError("Invalid index")
    print("HIYORICLIP_SHORTLIST "+json.dumps(preview(posts,limit=args.limit),ensure_ascii=False))

if __name__=="__main__":
    main()
