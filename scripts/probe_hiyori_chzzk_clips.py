"""Read-only check of public CHZZK clip listing and metadata for Hanayo Hiyori.
No media download, Discord posts, account authentication or production writes.
"""
import json,requests
from datetime import datetime,timezone
from urllib.parse import urlparse

CHANNEL="49d2c3cf8f0b2843d934ec15e82bb54c"
BASE="https://api.chzzk.naver.com/service/v1"
S=requests.Session()
S.headers.update({"User-Agent":"Mozilla/5.0 (compatible; AbyssBot-ClipProbe/1.0)","Accept":"application/json"})

def fetch(path,params=None):
    try:
        r=S.get(BASE+path,params=params,timeout=(5,12))
        result={"http":r.status_code,"bytes":len(r.content)}
        try:
            d=r.json()
        except ValueError:
            result["not_json"]=True
            return result,None
        result["code"]=d.get("code") if isinstance(d,dict) else None
        result["message"]=str(d.get("message",""))[:90] if isinstance(d,dict) else None
        return result,d.get("content") if isinstance(d,dict) else None
    except requests.RequestException as exc:
        return {"error":type(exc).__name__,"detail":str(exc)[:90]},None

def collect(order):
    info,body=fetch(f"/channels/{CHANNEL}/clips",
                    {"clipUID":"","filterType":"ALL","orderType":order,"page":0,"size":30})
    if isinstance(body,dict):
        entries=body.get("data")
        if isinstance(entries,list):
            info["returned"]=len(entries)
            info["next_page"]=bool((body.get("page") or {}).get("next"))
            info["fields_in_first"]=sorted(entries[0].keys()) if entries and isinstance(entries[0],dict) else []
            info["samples"]=[{k:item.get(k) for k in ("clipUID","videoId","clipTitle","createdDate","readCount","duration","adult","blindType","privateUserBlock")} for item in entries[:5] if isinstance(item,dict)]
            return info,entries
    return info,[]

def main():
    channel,_=fetch(f"/channels/{CHANNEL}")
    recent,posts=collect("RECENT")
    popular,most=collect("POPULAR")
    detail={}
    if posts:
        uid=posts[0].get("clipUID")
        if uid:
            detail,body=fetch(f"/clips/{uid}/detail")
            if isinstance(body,dict):
                detail["fields"]=sorted(body.keys())
                for key in ("clipUID","videoId","createdDate","readCount","likeCount","commentCount","clipTitle","ownerChannelId"):
                    if key in body:detail[key]=body[key]
                detail["has_playback_info"]=any("play" in str(k).lower() for k in body)
    report={"channel":CHANNEL,"channel_lookup":channel,"recent":recent,"popular":popular,"detail_first":detail,"utc":datetime.now(timezone.utc).isoformat()}
    print("CHZZK_HIYORI_CLIP_PROBE "+json.dumps(report,ensure_ascii=False))
if __name__=="__main__":main()
