"""Probe public CHZZK playback URLs; never download, print or cache signed URLs."""
import json, requests
import xml.etree.ElementTree as ET
from urllib.parse import urljoin
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
                    status={"http":r.status_code,"type":r.headers.get("Content-Type","")[:60],
                        "mpd_present":"<MPD" in r.text[:10000],"mp4_present":"video/mp4" in r.text[:10000]}
                    if r.status_code==200 and "<MPD" in r.text[:10000]:
                        tree=ET.fromstring(r.text)
                        formats=[]
                        for adap in tree.iter():
                            if adap.tag.split("}")[-1]!="AdaptationSet": continue
                            adap_mime=adap.attrib.get("mimeType","")
                            for rep in adap:
                                if rep.tag.split("}")[-1]!="Representation": continue
                                mime=rep.attrib.get("mimeType",adap_mime)
                                bases=[(x.text or "").strip() for x in rep.iter() if x.tag.split("}")[-1]=="BaseURL" and x.text]
                                for base_url in bases[:2]:
                                    target=urljoin(r.url,base_url)
                                    info={"mime":mime,"mp4_suffix":urlsplit(target).path.lower().endswith(".mp4"),
                                          "url_length":len(target),"direct_https":target.startswith("https://")}
                                    if mime=="video/mp4" and info["direct_https"] and info["mp4_suffix"]:
                                        try:
                                            head=s.head(target,timeout=(5,15),allow_redirects=True,
                                                headers={"Referer":f"https://chzzk.naver.com/clips/{UID}"})
                                            info["head_http"]=head.status_code
                                            info["head_content_type"]=head.headers.get("Content-Type","")[:55]
                                            info["head_content_length"]=head.headers.get("Content-Length","")
                                        except requests.RequestException as e:
                                            info["head_error"]=type(e).__name__
                                    formats.append(info)
                        status["representations"]=formats[:8]
                    results["neon_"+str(len(results))]=status
                except requests.RequestException as exc:
                    results["neon_error"]={"error":type(exc).__name__}
results["play_info"]=summary
print("CHZZK_DIRECT_PLAYBACK_DIAGNOSTIC "+json.dumps(results,ensure_ascii=False),flush=True)
