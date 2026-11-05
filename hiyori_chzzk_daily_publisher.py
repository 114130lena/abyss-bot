"""Publish one Hiyori CHZZK clip at 18:00 KST, safely and without role pings.

A complete *fresh* channel scan is mandatory. The daily time window ends
at 18:00 KST, and a durable reservation is persisted BEFORE sending, so
uncertain Discord HTTP responses cannot create automatic duplicate posts.
The same clip UID is never intentionally selected twice.
"""
import argparse
import json
import os
from datetime import datetime, timedelta, time as dtime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo
import requests
from dotenv import load_dotenv

import chzzk_hiyoriclip_collector as collector
from test_hiyori_chzzk_daily_card import make_card
from chzzk_clip_video import resolve_mp4

KST=ZoneInfo("Asia/Seoul")
ROOT=Path("/home/ubuntu/chzzk-bot")
STATE=ROOT/"creator_media"/"chzzk_hiyoriclip_daily_sent.json"
HOUR=18
MINUTE=0
MAX_DELAY_MINUTES=12

def scheduled_window(now):
    if now.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required")
    local=now.astimezone(KST)
    end=datetime.combine(local.date(),dtime(HOUR,MINUTE),tzinfo=KST)
    if local<end or local>=end+timedelta(minutes=MAX_DELAY_MINUTES):
        raise ValueError("Outside 18:00-18:12 KST delivery window")
    return end-timedelta(days=1),end

def select_clip(catalog, start, end, excluded):
    if not isinstance(catalog,dict):
        raise ValueError("Invalid CHZZK clip index")
    eligible=[]
    for clip in catalog.values():
        if not isinstance(clip,dict) or str(clip.get("id") or "") in excluded:
            continue
        created=collector.parse_iso(clip.get("created_at"))
        if (created is None or not start<=created.astimezone(KST)<end
                or clip.get("channel_id")!=collector.CHANNEL):
            continue
        if not isinstance(clip.get("url"),str) or not clip["url"].startswith("https://chzzk.naver.com/clips/"):
            continue
        if type(clip.get("views")) is not int or clip["views"]<0 or not clip.get("video_id"):
            continue
        eligible.append(clip)
    eligible.sort(key=lambda c:(-c["views"],c.get("created_at",""),str(c["id"])))
    return eligible[0] if eligible else None

def destination():
    load_dotenv(ROOT/".env")
    base=(os.getenv("CHZZK_DAILY_CLIP_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
    parts=urlsplit(base)
    if (parts.scheme!="https" or parts.hostname not in ("discord.com","discordapp.com")
            or not parts.path.startswith("/api/webhooks/") or parts.username or parts.password):
        raise ValueError("A valid Discord production webhook is required")
    thread=(os.getenv("CHZZK_DAILY_CLIP_THREAD_ID") or "").strip()
    q=dict(parse_qsl(parts.query,keep_blank_values=True))
    if thread:
        if not thread.isdigit():
            raise ValueError("Invalid dedicated clip thread ID")
        q["thread_id"]=thread
    elif "thread_id" in q:
        # Prevent inherited test threads being mistaken for the production destination.
        raise ValueError("Specify an explicit CHZZK_DAILY_CLIP_THREAD_ID")
    q["wait"]="true"
    return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(q),parts.fragment))

def _history():
    data=collector.read(STATE)
    days=data.get("days",{})
    if not isinstance(days,dict):
        raise ValueError("Malformed publication log")
    return {"version":1,"days":days}

def publish(*, now=None, dry_run=False, session=None, collect_fn=None, media_fn=None):
    now=now or datetime.now(KST)
    start,end=scheduled_window(now)
    day=end.strftime("%Y-%m-%d")
    history=_history()
    if day in history["days"]:
        return {"status":"already_attempted","day":day,
                "record_status":history["days"][day].get("status")}
    # Fresh comprehensive 24h scan, not yesterday's saved shortlist.
    report=(collect_fn or collector.collect)(now=now)
    if not report.get("complete_24h_scan") or not report.get("safe_for_daily_selection"):
        raise RuntimeError("CHZZK 24h scan was incomplete; no clip will be sent")
    checked=collector.parse_iso(report.get("checked_at"))
    if not checked or abs((now.astimezone(timezone.utc)-checked).total_seconds())>240:
        raise RuntimeError("CHZZK scan is stale; no clip will be sent")
    raw=collector.read(collector.INDEX).get("clips",{})
    previous_ids={str(v.get("clip_id")) for v in history["days"].values()
                  if isinstance(v,dict) and v.get("clip_id")}
    candidate=select_clip(raw,start,end,previous_ids)
    if candidate is None:
        return {"status":"no_unused_recent_clips","day":day}
    # Resolve signed MP4 shortly before the send; never store the signed URL.
    resolver=media_fn or resolve_mp4
    try:
        video=resolver(candidate["id"])
    except (requests.RequestException,ValueError,TypeError) as exc:
        video=None
        print("CHZZK_CLIP_MP4_FALLBACK "+type(exc).__name__,flush=True)
    from banner_manager import get_banner
    from creator_profiles import avatar
    payload=make_card(candidate,now=end,banner=get_banner("hanayo"),
                      avatar=avatar("hanayo"),video_url=video or "",preview=False)
    if dry_run:
        return {"status":"dry_run","day":day,"clip_id":candidate["id"],
                "views_used_for_selection":candidate["views"],"mp4_attached":bool(video)}
    url=destination()
    # Crash-safe at-most-once semantics: uncertain responses require manual
    # reconciliation, not another automated webhook POST.
    history["days"][day]={"status":"attempted","clip_id":candidate["id"],
                          "created_at":now.astimezone(KST).isoformat()}
    collector._write(STATE,history)
    session=session or requests.Session()
    try:
        resp=session.post(url,json=payload,timeout=(5,25))
        resp.raise_for_status()
        received=resp.json()
        msgid=received.get("id") if isinstance(received,dict) else None
        if not msgid:
            raise RuntimeError("Discord response missing message ID")
    except (requests.RequestException,ValueError,RuntimeError) as exc:
        # Do NOT retry; the message may have arrived even if HTTP timed out.
        history["days"][day]["status"]="delivery_uncertain"
        history["days"][day]["failure_type"]=type(exc).__name__
        collector._write(STATE,history)
        raise RuntimeError("Delivery unconfirmed; manual review required; retry suppressed") from exc
    history["days"][day]["status"]="sent"
    history["days"][day]["message_id"]=str(msgid)
    collector._write(STATE,history)
    return {"status":"sent","day":day,"clip_id":candidate["id"],
            "message_id":str(msgid),"mp4_attached":bool(video)}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--dry-run",action="store_true",help="Check selection without posting")
    p.add_argument("--show",action="store_true",help="Read publication state only")
    args=p.parse_args()
    if args.show:
        result=_history()
    else:
        result=publish(dry_run=args.dry_run)
    print("HIYORI_DAILY_1800 "+json.dumps(result,ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
