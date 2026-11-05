"""Maintain one editable '오늘의 히요리 클립' card with the EXISTING guild-banner bot.

Every four hours 12:05, 16:05, 20:05 KST (sleep overnight). Scan the last 24 hours completely and select highest-view
CHZZK clip. PATCH the same bot-owned message, refreshing signed MP4 if possible.
First run creates one message; uncertain creation is never automatically retried.
No role pings, no new Discord app, no changes to guild banner rotation.
"""
import argparse
import json
import os
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
import requests
from dotenv import dotenv_values

import chzzk_hiyoriclip_collector as collector
from test_hiyori_chzzk_daily_card import make_card
from chzzk_clip_video import resolve_mp4

KST=ZoneInfo("Asia/Seoul")
ROOT=Path("/home/ubuntu/chzzk-bot")
STATE=ROOT/"creator_media"/"hiyori_hourly_clip_message.json"
BANNER_AUTH=ROOT/".guild_banner_auth.env"
CHANNEL="1567760518977167501"
GUILD="919216649880633465"
API="https://discord.com/api/v10"
MARKER="오늘의 히요리 클립"
WINDOW=timedelta(hours=24)
ACTIVE_HOURS=(8,18)
SHOW_TOP=10
HISTORY_MAX=80

def _now(now=None):
    current=now or datetime.now(KST)
    if current.tzinfo is None:
        raise ValueError("Current time must include timezone")
    return current.astimezone(KST)

def _slot(now):
    return _now(now).strftime("%Y-%m-%dT%H")

def ranked(catalog,now):
    now=_now(now).astimezone(timezone.utc)
    start=now-WINDOW
    result=[]
    if not isinstance(catalog,dict):
        raise ValueError("CHZZK cache has unexpected shape")
    for item in catalog.values():
        if not isinstance(item,dict) or item.get("channel_id")!=collector.CHANNEL:
            continue
        when=collector.parse_iso(item.get("created_at"))
        if when is None or not start<=when<=now:
            continue
        views=item.get("views")
        if type(views) is not int or views<0 or not item.get("video_id"):
            continue
        if item.get("url")!="https://chzzk.naver.com/clips/"+str(item.get("id")):
            continue
        result.append(item)
    return sorted(result,key=lambda x:(-x["views"],x.get("created_at",""),x["id"]))

def choose(catalog,now):
    top=ranked(catalog,now)
    return top[0] if top else None

def _markdown_title(value):
    title=str(value or "제목 없음").replace("\\","\\\\").replace("\n"," ")[:80]
    for ch in ("[","]","(",")","*","_","~","`"):
        title=title.replace(ch,"\\"+ch)
    return title

def _clip_link(clip):
    return "["+_markdown_title(clip.get("title"))+"](https://chzzk.naver.com/clips/"+str(clip["id"])+")"

def _extra_links(top,history):
    candidates=top[:SHOW_TOP]
    lines=["**🎞️ 영상 목록 · "+str(len(candidates))+"개**"]
    lines.extend(str(index)+". "+_clip_link(item) for index,item in enumerate(candidates,1))
    old=[]
    currently={str(item["id"]) for item in top[:SHOW_TOP]}
    for item in reversed(history):
        if isinstance(item,dict) and item.get("id") and item["id"] not in currently:
            old.append(item)
        if len(old)>=8:
            break
    if old:
        lines.extend(["","**🕘 이전에 소개한 클립**"])
        lines.extend("• "+_clip_link(item) for item in old)
    return "\n".join(lines)[:3700]


def _auth():
    values=dotenv_values(BANNER_AUTH)
    token=values.get("DISCORD_BOT_TOKEN")
    if not isinstance(token,str) or len(token)<30:
        raise ValueError("Existing guild-banner bot token unavailable")
    return token

def _session(token):
    s=requests.Session()
    s.headers.update({"Authorization":"Bot "+token,"Accept":"application/json","Content-Type":"application/json"})
    return s

def _endpoint(message_id=""):
    return API+"/channels/"+CHANNEL+"/messages"+("/"+message_id if message_id else "")

def _identity(session):
    me=session.get(API+"/users/@me",timeout=(5,15))
    me.raise_for_status()
    bot_id=me.json().get("id")
    channel=session.get(API+"/channels/"+CHANNEL,timeout=(5,15))
    channel.raise_for_status()
    metadata=channel.json()
    if metadata.get("guild_id")!=GUILD or metadata.get("type")!=0 or not str(bot_id).isdigit():
        raise ValueError("Banner bot or requested clip channel identity mismatch")
    return bot_id

def _is_ours(message,bot_id):
    if not isinstance(message,dict) or str((message.get("author") or {}).get("id"))!=str(bot_id):
        return False
    components=message.get("components") or []
    return MARKER in json.dumps(components,ensure_ascii=False)

def _find_existing(session,bot_id):
    response=session.get(_endpoint(),params={"limit":100},timeout=(5,15))
    response.raise_for_status()
    items=response.json()
    if not isinstance(items,list):
        raise ValueError("Unexpected Discord channel history")
    found=[msg for msg in items if _is_ours(msg,bot_id)]
    if len(found)>1:
        raise RuntimeError("Multiple bot-owned clip cards found; refuse to create or edit")
    return str(found[0]["id"]) if found else None

def _saved():
    obj=collector.read(STATE)
    return obj if isinstance(obj,dict) else {}

def _persist(value):
    collector._write(STATE,value)

def _ensure_message_id(session,bot_id,state):
    mid=state.get("message_id")
    if mid:
        r=session.get(_endpoint(str(mid)),timeout=(5,15))
        if r.status_code==404:
            # Do not create a second message when an old card was deleted:
            # explicitly inspect channel history for a replacement.
            return _find_existing(session,bot_id)
        r.raise_for_status()
        if not _is_ours(r.json(),bot_id):
            raise RuntimeError("Stored clip message is not authored by existing banner bot")
        return str(mid)
    return _find_existing(session,bot_id)

def refresh(*,now=None,session=None,collect_fn=None,video_fn=None,banner="",avatar="",dry_run=False):
    now=_now(now)
    slot=_slot(now)
    if now.hour not in ACTIVE_HOURS or now.minute<5:
        return {"status":"outside_active_hours","slot":slot}
    state=_saved()
    if state.get("last_success_slot")==slot:
        return {"status":"already_updated_this_hour","slot":slot,"message_id":state.get("message_id")}
    # A new run always requires a recent COMPLETE 24h CHZZK crawl.
    report=(collect_fn or collector.collect)(now=now)
    if not report.get("complete_24h_scan") or not report.get("safe_for_daily_selection"):
        raise RuntimeError("CHZZK recent 24-hour scan incomplete; retaining previous card")
    checked=collector.parse_iso(report.get("checked_at"))
    if not checked or abs((checked-now.astimezone(timezone.utc)).total_seconds())>240:
        raise RuntimeError("CHZZK data freshness check failed")
    source=collector.read(collector.INDEX).get("clips",{})
    top=ranked(source,now)
    chosen=top[0] if top else None
    if not chosen:
        return {"status":"no_eligible_recent_clips_keep_previous","slot":slot}
    if dry_run:
        return {"status":"dry_run","slot":slot,"clip_id":chosen["id"],"views":chosen["views"]}
    session=session or _session(_auth())
    bot_id=_identity(session)
    # Reconcile a previous uncertain first POST before any new write.
    msgid=_ensure_message_id(session,bot_id,state)
    if not msgid and state.get("create_pending"):
        return {"status":"pending_first_post_manual_reconciliation","slot":slot}
    try:
        video=(video_fn or resolve_mp4)(chosen["id"])
    except (ValueError,TypeError,requests.RequestException) as exc:
        video=None
        print("HOURLY_HIYORI_CLIP_VIDEO_FALLBACK "+type(exc).__name__,flush=True)
    # Reuse the existing creator/brand presentation, but no rank/metrics/time.
    card=make_card(chosen,now=now,banner=banner,avatar=avatar,video_url=video or "",
                   preview=False,footer_text="12:05 · 16:05 · 20:05 갱신")
    # One gallery, at most ten direct-play MP4s. Avoid banner gallery as that
    # would consume one of Discord's per-message ten media slots.
    media_items=[]
    for item in top[:SHOW_TOP]:
        try:
            clip_video=(video if item["id"]==chosen["id"] else (video_fn or resolve_mp4)(item["id"]))
        except (ValueError,TypeError,requests.RequestException) as exc:
            clip_video=None
            print("HIYORI_GALLERY_MEDIA_FALLBACK "+type(exc).__name__,flush=True)
        if clip_video and clip_video.startswith("https://") and urlsplit(clip_video).path.lower().endswith(".mp4"):
            media_items.append({"media":{"url":clip_video},
                                "description":str(item.get("title") or "히요리 클립")[:200]})
    # Keep the old message instead of replacing playable videos with stills
    # when the source CDN is unreliable. No falsely advertised full gallery.
    if len(media_items)<min(SHOW_TOP,len(top)):
        return {"status":"video_sources_incomplete_keep_previous",
                "slot":slot,"available":len(media_items),
                "required":min(SHOW_TOP,len(top))}
    parts=card["components"][0]["components"]
    parts[:]=[part for part in parts if not (part.get("type")==12)]
    # Put all videos together in the original media position.
    parts.insert(4,{"type":12,"items":media_items})
    old_history=state.get("featured_history",[])
    if not isinstance(old_history,list):
        old_history=[]
    extras=_extra_links(top,old_history)
    if extras:
        card["components"][0]["components"].insert(-1,{"type":10,"content":extras})
    if len(card["components"][0]["components"])>10:
        raise RuntimeError("Discord V2 card components exceed maximum")
    # Bot token REST endpoints reject webhook-only username, so remove it.
    card.pop("username",None)
    card["allowed_mentions"]={"parse":[]}
    was_created=not bool(msgid)
    if not msgid:
        # Reserve the first-message creation BEFORE HTTP request. If the POST
        # succeeds and the response is lost, next run adopts the existing card.
        state.update({"version":1,"channel_id":CHANNEL,"create_pending":True,
                      "pending_slot":slot,"pending_clip_id":chosen["id"]})
        _persist(state)
        response=session.post(_endpoint(),json=card,timeout=(5,25))
        response.raise_for_status()
        created=response.json()
        if not _is_ours(created,bot_id):
            raise RuntimeError("Discord new-message response identity mismatch; manual check needed")
        msgid=str(created["id"])
    else:
        # Editing one existing message never creates new channel posts.
        response=session.patch(_endpoint(msgid),json=card,timeout=(5,25))
        response.raise_for_status()
        updated=response.json()
        if str(updated.get("id"))!=str(msgid) or not _is_ours(updated,bot_id):
            raise RuntimeError("Discord updated-message response mismatch")
    already_seen={item.get("id") for item in old_history if isinstance(item,dict)}
    for item in top[:SHOW_TOP]:
        if item["id"] not in already_seen:
            old_history.append({"id":item["id"],"title":item.get("title",""),
                                "featured_at":now.isoformat()})
            already_seen.add(item["id"])
    state.update({"featured_history":old_history[-HISTORY_MAX:],
                  "version":1,"channel_id":CHANNEL,"message_id":str(msgid),
                  "create_pending":False,"last_success_slot":slot,
                  "last_clip_id":chosen["id"],"last_updated_at":now.isoformat()})
    _persist(state)
    return {"status":"created" if was_created else "updated",
            "slot":slot,"message_id":str(msgid),"clip_id":chosen["id"],
            "direct_video":bool(video)}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--show",action="store_true")
    p.add_argument("--dry-run",action="store_true")
    args=p.parse_args()
    if args.show:
        print("HIYORI_HOURLY_CLIP "+json.dumps(_saved(),ensure_ascii=False))
        return
    from banner_manager import get_banner
    from creator_profiles import avatar
    result=refresh(dry_run=args.dry_run,banner=get_banner("hanayo"),avatar=avatar("hanayo"))
    print("HIYORI_HOURLY_CLIP "+json.dumps(result,ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
