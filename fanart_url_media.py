"""Select public X media URLs for Discord V2 cards without re-uploading image bytes."""
import copy,json,os,random
from urllib.parse import urlsplit
from pathlib import Path
from fanart_discovery import BASE, QUEUE, read

def _usable(url):
    p=urlsplit(str(url or ""))
    return p.scheme=="https" and p.hostname=="pbs.twimg.com" and p.path.startswith("/media/")

def candidates(group):
    result=[]
    for item in read(QUEUE).values():
        if not isinstance(item,dict) or item.get("group")!=group or item.get("review_status")=="blocked":
            continue
        for url in item.get("image_urls",[])[:4]:
            if _usable(url):
                result.append((url,item.get("artist",""),item.get("post_url","")))
    return result

def apply(payload, group=None):
    out=copy.deepcopy(payload)
    containers=[c for c in out.get("components",[]) if c.get("type")==17]
    if not containers:return out,None
    parts=containers[-1].get("components",[])
    section=next((p for p in parts if p.get("type")==9 and p.get("accessory",{}).get("type")==11),None)
    if group is None and section:
        signature=" ".join(p.get("content","") for p in section.get("components",[])).lower()
        group=("hanayo" if ("히요리" in signature or "hanayo" in signature) else
               "sakihane" if ("후야" in signature or "sakihane" in signature) else None)
    if not group or not section:return out,None
    pool=candidates(group)
    if not pool:return out,None
    # Independent random picks for avatar and footer, with identifiable credits.
    avatar=random.SystemRandom().choice(pool)
    banner=random.SystemRandom().choice([x for x in pool if x[0]!=avatar[0]] or pool)
    section["accessory"]["media"]["url"]=avatar[0]
    for i,part in enumerate(parts[:-1]):
        if part.get("type")==10 and "OFFICIAL LINKS" in part.get("content",""):
            gallery=parts[i+1]
            if gallery.get("type")==12:
                gallery["items"]=[{"media":{"url":banner[0]},"description":"팬아트 원본 이미지 · "+banner[1]}]
                part["content"]+="\\n-# 🎨 프로필 "+avatar[1]+" · [원본]("+avatar[2]+") | 배너 "+banner[1]+" · [원본]("+banner[2]+")"
                return out,{"group":group,"avatar":avatar,"banner":banner}
    return payload,None
