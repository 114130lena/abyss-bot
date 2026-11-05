"""Read-only CHZZK clip playback metadata probe. Never emits URLs or downloads media."""
import json
import requests

UID="427pARq04E"
VIDEO="68334BD1AE1A2290D1670C6D1B1808F8CCED"
CHANNEL="49d2c3cf8f0b2843d934ec15e82bb54c"
BASE="https://api-videohub.naver.com/shortformhub/feeds/"
params={
  "seedType":"SPECIFIC","serviceType":"CHZZK","seedMediaId":VIDEO,
  "mediaType":"VOD","panelType":"sdk_chzzk",
  "referer":f"https://chzzk.naver.com/clips/{UID}",
  "recType":"CHZZK",
  "recId":json.dumps({"seedClipUID":UID,"fromType":"CHANNEL","listType":"CHANNEL",
                      "orderType":"RECENT","filterType":"ALL","channelId":CHANNEL},separators=(",",":")),
  "enableReverse":"false","adAllowed":"Y",
  "clickNsc":"chzzk_channel_clip","clickArea":"clip_item","deviceType":"html5_mo"}
session=requests.Session()
for v in ("v5","v9"):
    try:
        r=session.get(BASE+v+"/card",params=params,timeout=18,
           headers={"User-Agent":"Mozilla/5.0","Accept":"application/json",
                    "Origin":"https://chzzk.naver.com","Referer":f"https://chzzk.naver.com/clips/{UID}"})
        data=r.json()
        result={"http":r.status_code,"root_keys":list(data)[:20]}
        if isinstance(data.get("card"),dict):
            card=data["card"]
            result["card_keys"]=list(card)[:20]
            vod=(card.get("content") or {}).get("vod") or {}
            result["vod_keys"]=list(vod)[:20]
            result["playable"]=vod.get("playable")
            playback=vod.get("playback") or {}
            result["playback_keys"]=list(playback)[:20]
            result["has_mpd"]=bool(playback.get("MPD"))
            result["has_video_list"]=bool((playback.get("videos") or {}).get("list"))
            result["video_list_count"]=len((playback.get("videos") or {}).get("list",[]))
        result["message"]=str(data.get("message") or data.get("error") or "")[:100]
        print("CHZZK_PLAY_PROBE "+json.dumps({"version":v,**result},ensure_ascii=False),flush=True)
    except Exception as exc:
        print("CHZZK_PLAY_PROBE "+json.dumps({"version":v,"error":type(exc).__name__}),flush=True)
