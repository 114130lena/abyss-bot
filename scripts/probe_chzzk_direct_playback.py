"""Probe public CHZZK playback URLs; never download, print or cache signed URLs."""
import json, requests
from urllib.parse import urlsplit
UID="KvK9UOrTYv"
s=requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://chzzk.naver.com/","Accept":"application/json"})
results={}
def check(name,url,params=None):
    try:
        r=s.get(url,params=params,timeout=(5,18))
        out={"http":r.status_code,"type":r.headers.get("Content-Type","")[:55]}
        try: obj=r.json()
        except ValueError: return out,None
        if isinstance(obj,dict):
            out["code"]=obj.get("code")
            out["keys"]=list(obj)[:12]
        return out,obj
    except requests.RequestException as e: return {"error":type(e).__name__},None
base="https://api.chzzk.naver.com"
summary,data=check("play_info",f"{base}/service/v1/play-info/clip/{UID}")
if isinstance(data,dict):
    content=data.get("content")
    if isinstance(content,dict):
        summary["content_keys"]=list(content)[:30]
        summary["inKey_present"]=bool(content.get("inKey"))
        summary["videoId_present"]=bool(content.get("videoId"))
        if content.get("inKey") and content.get("videoId"):
            video=str(content["videoId"])
            key=str(content["inKey"])
            for api in ("https://apis.naver.com/neonplayer/vodplay/v2/playback/"+video,
                        "https://apis.naver.com/neonplayer/vodplay/v2/playback/"+video+".mpd"):
                try:
                    r=s.get(api,params={"key":key},timeout=(5,15),headers={"Accept":"application/dash+xml"})
                    results["neon_"+str(len(results))]={"http":r.status_code,"type":r.headers.get("Content-Type","")[:60],
                        "mpd_present":"<MPD" in r.text[:10000],"mp4_present":"video/mp4" in r.text[:10000]}
                except requests.RequestException as exc:
                    results["neon_error"]={"error":type(exc).__name__}
results["play_info"]=summary
print("CHZZK_DIRECT_PLAYBACK_DIAGNOSTIC "+json.dumps(results,ensure_ascii=False),flush=True)
