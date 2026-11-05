"""Send only a mention-free test card of the top last-24h CHZZK Hiyori clip.

Uses the existing Discord test thread; no daily scheduler or production alert
changes. Discord Components V2: creator header -> topic -> title -> thumbnail
-> stats -> source button -> compact footer.
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

KST=ZoneInfo("Asia/Seoul")
THREAD="1566335914500816988"
FLAGS=1<<15

def _txt(value):
    return {"type":10,"content":str(value)[:4000]}

def make_card(clip, *, now=None, banner="", avatar="", video_url=""):
    now=(now or datetime.now(KST)).astimezone(KST)
    if not isinstance(clip,dict) or not clip.get("id"):
        raise ValueError("No verified CHZZK clip")
    url=str(clip.get("url") or "")
    parsed=urlsplit(url)
    if parsed.scheme!="https" or parsed.hostname!="chzzk.naver.com" or parsed.path!="/clips/"+str(clip["id"]):
        raise ValueError("Invalid clip URL")
    title=str(clip.get("title") or "제목 없음").strip()[:180].replace("\n"," ")
    for char in "\\*[]()_~`":
        title=title.replace(char,"\\"+char)
    views=clip.get("views")
    if type(views) is not int or views<0:
        raise ValueError("CHZZK view count unavailable")
    header="## 하나요 히요리 HANAYO HIYORI\n-# @HanayoHiyori\n## STELLIVE · ABYSS"
    if avatar.startswith("https://"):
        head={"type":9,"components":[_txt(header)],
              "accessory":{"type":11,"media":{"url":avatar},"description":"히요리 프로필"}}
    else:
        head=_txt(header)
    components=[head,{"type":14,"divider":True,"spacing":1},
        _txt("**🎬 오늘의 히요리 클립**\n-# "+now.strftime("%Y.%m.%d")+" · CHZZK · TEST"),
        _txt("## ["+title+"]("+url+")"),
    ]
    thumbnail=str(clip.get("thumbnail_url") or "")
    media=video_url if video_url.startswith("https://") else thumbnail
    if media.startswith("https://"):
        components.append({"type":12,"items":[{"media":{"url":media},"description":"치지직 클립 영상" if video_url else "치지직 클립 썸네일"}]})
    components.append(_txt("👁️ **조회수 "+format(views,",")+"회**  ·  🏆 최근 24시간 수집 클립 조회수 1위\n-# 선정 기준 · 조회수 (수집 시점 기준)"))
    components.append({"type":14,"divider":True,"spacing":1})
    components.append({"type":1,"components":[{"type":2,"style":5,"label":"🎬 치지직 클립 보기","url":url}]})
    if banner.startswith("https://") and len(components)<9:
        components.append({"type":12,"items":[{"media":{"url":banner},"description":"히요리 공식 채널 배너"}]})
    components.append(_txt("-# ABYSS BOT · 매일 12:00 KST · TEST PREVIEW"))
    if len(components)>10:
        raise ValueError("Discord component count exceeded")
    return {"username":"어비스봇","flags":FLAGS,"components":[
        {"type":17,"accent_color":0xB794D8,"components":components}
    ],"allowed_mentions":{"parse":[]}}

def choose_clip(cache, *, now=None):
    now=(now or datetime.now(KST)).astimezone(KST)
    clips=cache.get("clips",{})
    if not isinstance(clips,dict):
        raise ValueError("Malformed cache")
    candidates=[]
    for x in clips.values():
        try:
            created=datetime.fromisoformat(str(x.get("created_at")).replace("Z","+00:00")).astimezone(KST)
            if now-timedelta(hours=24)<=created<=now and x.get("url") and type(x.get("views")) is int:
                candidates.append(x)
        except (TypeError,ValueError,AttributeError):
            continue
    if not candidates:
        raise ValueError("No recent CHZZK clips")
    return max(candidates,key=lambda x:(x["views"],x.get("created_at",""),x.get("id","")))

if __name__=="__main__":
    import argparse
    import requests
    from dotenv import load_dotenv
    from urllib.parse import parse_qsl,urlunsplit,urlencode
    p=argparse.ArgumentParser()
    p.add_argument("--send",action="store_true")
    opts=p.parse_args()
    root=Path("/home/ubuntu/chzzk-bot")
    cache=json.loads((root/"creator_media/chzzk_hiyoriclip_posts.json").read_text("utf-8"))
    clip=choose_clip(cache)
    if opts.send:
        load_dotenv(root/".env")
        from banner_manager import get_banner
        from creator_profiles import avatar
        from resilience import send_webhook
        base=os.getenv("TEST_DISCORD_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL","")
        parts=urlsplit(base)
        if parts.scheme!="https" or parts.hostname not in ("discord.com","discordapp.com") or not parts.path.startswith("/api/webhooks/"):
            raise ValueError("Invalid Discord test webhook")
        params=dict(parse_qsl(parts.query,keep_blank_values=True))
        params["thread_id"]=THREAD
        dest=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(params),parts.fragment))
        from chzzk_clip_video import resolve_mp4
        try:
            video_url=resolve_mp4(clip["id"])
        except (ValueError,requests.RequestException) as exc:
            video_url=None
            print("CHZZK_VIDEO_RESOLVE_FAILED "+type(exc).__name__,flush=True)
        payload=make_card(clip,banner=get_banner("hanayo"),avatar=avatar("hanayo"),video_url=video_url or "")
        send_webhook(dest,payload,attempts=1)
    print("CHZZK_CLIP_TEST "+json.dumps({"sent":opts.send,"thread":THREAD,"clip_id":clip["id"],
        "title":clip.get("title"),"views":clip.get("views"),"url":clip.get("url"),
        "has_thumbnail":bool(clip.get("thumbnail_url")),"video_resolution_attempted":opts.send,"video_gallery_requested":bool(video_url) if opts.send else False},ensure_ascii=False))
