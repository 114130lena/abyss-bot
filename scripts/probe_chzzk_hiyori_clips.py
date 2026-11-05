"""Read-only CHZZK clip API diagnostic for Hanayo Hiyori channel. No Discord posting."""
import json
import requests
from datetime import datetime, timezone

CHANNEL = "49d2c3cf8f0b2843d934ec15e82bb54c"
BASE = "https://api.chzzk.naver.com"
HEADERS={"User-Agent":"Mozilla/5.0 (compatible; abyss-bot clip research/1.0)","Accept":"application/json"}

def fetch(s,url,params=None):
    r=s.get(url,params=params,headers=HEADERS,timeout=(5,20))
    result={"http":r.status_code,"type":r.headers.get("Content-Type","")[:60]}
    try:
        data=r.json()
    except ValueError:
        result["response_excerpt"]=r.text[:140]
        return result,None
    result["code"]=data.get("code") if isinstance(data,dict) else None
    result["message"]=str(data.get("message",""))[:140] if isinstance(data,dict) else ""
    return result,data.get("content") if isinstance(data,dict) else None

def run():
    s=requests.Session()
    results={}
    found=[]
    for order in ("RECENT","POPULAR"):
        params={"filterType":"ALL","orderType":order,"page":0,"size":30}
        summary,content=fetch(s,f"{BASE}/service/v1/channels/{CHANNEL}/clips",params)
        if isinstance(content,dict):
            clips=content.get("data")
            summary["count"]=len(clips) if isinstance(clips,list) else None
            summary["has_next"]=bool((content.get("page") or {}).get("next"))
            if isinstance(clips,list):
                summary["samples"]=[{
                    "clipUID":x.get("clipUID"),"videoId":x.get("videoId"),
                    "title":str(x.get("clipTitle",""))[:100],
                    "createdDate":x.get("createdDate"),"readCount":x.get("readCount"),
                    "duration":x.get("duration"),"ownerChannelId":x.get("ownerChannelId"),
                    "thumbnail_present":bool(x.get("thumbnailImageUrl")),
                } for x in clips[:4] if isinstance(x,dict)]
                found+=clips
        results[order]=summary
    if found:
        uid=found[0].get("clipUID")
        if uid:
            summary,content=fetch(s,f"{BASE}/service/v1/clips/{uid}/detail")
            if isinstance(content,dict):
                summary["field_names"]=sorted(content.keys())[:50]
                summary["clipUID"]=uid
                summary["videoId"]=content.get("videoId")
                summary["readCount"]=content.get("readCount")
                summary["likeCount"]=content.get("likeCount")
                summary["createdDate"]=content.get("createdDate")
                summary["ownerChannelId"]=content.get("ownerChannelId")
                summary["clipTitle"]=str(content.get("clipTitle",""))[:100]
                video=content.get("videoId") or found[0].get("videoId")
                if video:
                    vmeta,play=fetch(s,"https://api-videohub.naver.com/shortformhub/feeds/v9/card",
                                      {"seedMediaId":video,"seedType":"SPECIFIC","serviceType":"CHZZK"})
                    vmeta["has_card"]=bool(play)
                    if isinstance(play,dict):
                        vmeta["keys"]=sorted(play.keys())[:20]
                    results["videohub"]=vmeta
            results["detail"]=summary
    results["checked_at"]=datetime.now(timezone.utc).isoformat()
    print("CHZZK_CLIP_PROBE "+json.dumps(results,ensure_ascii=False),flush=True)

if __name__=="__main__":
    run()
