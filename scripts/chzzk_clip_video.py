"""Resolve short-lived public CHZZK clip MP4 URL for immediate Discord V2 preview.

No video download, no authentication cookies, no storage of signed URLs.
Never print the URL (it contains an expiring access token).
"""
import xml.etree.ElementTree as ET
from urllib.parse import urljoin,urlsplit
import requests

API="https://api.chzzk.naver.com"
PLAY="https://apis.naver.com/neonplayer/vodplay/v2/playback/"

def resolve_mp4(clip_id, *, session=None):
    if not isinstance(clip_id,str) or not 6<=len(clip_id)<=40 or not clip_id.replace("_","").replace("-","").isalnum():
        raise ValueError("Invalid clip identifier")
    session=session or requests.Session()
    headers={"User-Agent":"Mozilla/5.0","Referer":"https://chzzk.naver.com/","Accept":"application/json"}
    resp=session.get(f"{API}/service/v1/play-info/clip/{clip_id}",headers=headers,timeout=(5,15))
    resp.raise_for_status()
    body=resp.json()
    content=body.get("content") if isinstance(body,dict) and body.get("code")==200 else None
    if not isinstance(content,dict) or content.get("adult") or content.get("naverOnly"):
        return None
    in_key=content.get("inKey")
    video_id=content.get("videoId")
    if not isinstance(in_key,str) or not isinstance(video_id,str) or not in_key or not video_id:
        return None
    if not video_id.isalnum():
        return None
    manifest=session.get(PLAY+video_id,params={"key":in_key},headers={"Accept":"application/dash+xml","User-Agent":"Mozilla/5.0"},
                         timeout=(5,15))
    manifest.raise_for_status()
    if len(manifest.content)>1_000_000:
        raise ValueError("Oversized DASH metadata")
    root=ET.fromstring(manifest.content)
    candidates=[]

    for adaptation in root.iter():
        if adaptation.tag.split("}")[-1]!="AdaptationSet":
            continue
        mime=adaptation.attrib.get("mimeType","")
        for representation in adaptation.iter():
            if representation.tag.split("}")[-1]!="Representation":
                continue
            if representation.attrib.get("mimeType",mime)!="video/mp4":
                continue
            for base in representation.iter():
                if base.tag.split("}")[-1]!="BaseURL" or not base.text:
                    continue
                url=urljoin(manifest.url,base.text.strip())
                parsed=urlsplit(url)
                if (parsed.scheme=="https" and parsed.hostname and
                        (parsed.hostname.endswith("-smp-vod.akamaized.net") or parsed.hostname.endswith("-smp-vod.pstatic.net")) and
                        parsed.path.lower().endswith(".mp4") and
                        parsed.username is None and parsed.password is None and len(url)<=2048):
                    candidates.append(url)
    for url in candidates[:3]:
        try:
            check=session.head(url,headers={"Referer":f"https://chzzk.naver.com/clips/{clip_id}"},
                               timeout=(5,12),allow_redirects=True)
            if check.status_code==200 and "video/mp4" in check.headers.get("Content-Type",""):
                return url
        except requests.RequestException:
            continue
    return None
