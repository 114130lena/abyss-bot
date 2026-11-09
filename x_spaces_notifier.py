"""Track scheduled and live X Spaces with X API creator IDs.

Requires X_BEARER_TOKEN, HANAYO_X_USER_ID, SAKIHANE_X_USER_ID.
Unavailable API causes a logged skip and does not affect other alerts.
"""
import json,logging,os,time
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
import requests
from dotenv import load_dotenv
from resilience import send_webhook,save_json,load_json
from discord_v2 import alert
from banner_manager import get_banner
from creator_profiles import handle,avatar

BASE=Path(os.getenv("BOT_DATA_DIR","/home/ubuntu/chzzk-bot"))
load_dotenv(BASE/".env")
STATE=BASE/"x_spaces_seen.json"
WEBHOOK=os.getenv("DISCORD_WEBHOOK_URL","")
POLL=max(60,int(os.getenv("X_SPACES_POLL_INTERVAL","120")))
NEXT=0
ACCOUNT={"hanayo":os.getenv("HANAYO_X_USER_ID",""),"sakihane":os.getenv("SAKIHANE_X_USER_ID","")}
NAMES={"hanayo":"하나요 히요리","sakihane":"사키하네 후야"}

def check_spaces():
    global NEXT
    now=time.monotonic()
    if now<NEXT:return
    NEXT=now+POLL
    token=os.getenv("X_BEARER_TOKEN","")
    owners={v:k for k,v in ACCOUNT.items() if v.isdigit()}
    if not token or not owners or not WEBHOOK.startswith("https://"):
        return
    response=requests.get("https://api.x.com/2/spaces/by/creator_ids",
        params={"user_ids":",".join(owners),
                "space.fields":"id,title,state,scheduled_start,started_at,creator_id"},
        headers={"Authorization":"Bearer "+token},timeout=15)
    if response.status_code in (401,403,429):
        logging.warning("X Spaces API unavailable (%d); no alerts sent",response.status_code)
        NEXT=time.monotonic()+900
        return
    response.raise_for_status()
    entries=response.json().get("data",[])
    if not isinstance(entries,list):return
    previous=load_json(STATE,{})
    if not isinstance(previous,dict):previous={}
    observed={}
    for space in entries:
        sid=str(space.get("id",""))
        group=owners.get(str(space.get("creator_id","")))
        stage=space.get("state")
        if not group or not sid or stage not in ("live","scheduled"):continue
        key=group+":"+sid+":"+stage
        observed[key]=True
        if key in previous:continue
        # On first activation do not flood the channel with existing spaces.
        if not STATE.exists():continue
        url="https://x.com/i/spaces/"+sid
        title=("🎙️ X SPACE · LIVE" if stage=="live" else "📅 X SPACE · 예정")
        payload=alert("x",NAMES[group],get_banner(group),
            role_id="",title=title+" · "+str(space.get("title") or "제목 없음")[:200],
            body=("실시간 스페이스가 시작되었습니다." if stage=="live" else
                  "예정된 스페이스가 등록되었습니다.")+
                 ("\\n예정 시작: "+str(space["scheduled_start"]) if stage=="scheduled" and space.get("scheduled_start") else ""),
            url=url,handle=handle(group),avatar=avatar(group),ping=False)
        payload["_abyss_event_key"] = "x-space:" + group + ":" + sid + ":" + stage
        send_webhook(WEBHOOK,payload)
    previous.update(observed)
    save_json(STATE,previous)
