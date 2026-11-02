"""Select public X media URLs for Discord V2 cards without re-uploading image bytes."""
import copy,json,os,random,threading
from datetime import datetime,timezone
from urllib.parse import urlsplit
from pathlib import Path
from fanart_discovery import BASE, QUEUE, read, write

HISTORY=BASE/'fanart_url_used.json'
LOCK=threading.RLock()


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
    # Never repeat an already delivered fanart, even after weekly cache cleanup.
    with LOCK:
        used=read(HISTORY).get(group,{})
        if not isinstance(used,dict):used={}
        fresh=[x for x in pool if x[2] not in used]
        if not fresh:return payload,None
        picked=random.SystemRandom().choice(fresh)
    section["accessory"]["media"]["url"]=picked[0]
    for i,part in enumerate(parts[:-1]):
        if part.get("type")==10 and "OFFICIAL LINKS" in part.get("content",""):
            gallery=parts[i+1]
            if gallery.get("type")!=12:
                continue
            gallery["items"]=[{"media":{"url":picked[0]},"description":"팬아트 · "+picked[1]}]
            # Keep the signature short. Move the original post URL into the button row.
            part["content"]="-# 🎨 FAN ART · "+picked[1]
            source=picked[2]
            if not (isinstance(source,str) and source.startswith("https://x.com/")):
                return payload,None
            button={"type":2,"style":5,"label":"원본 게시물","url":source}
            row=next((x for x in reversed(parts) if x.get("type")==1 and isinstance(x.get("components"),list)),None)
            if row and len(row["components"])<5:
                row["components"]=[button]+[x for x in row["components"] if x.get("label")!="원본 게시물"]
            elif not row and len(parts)<10:
                parts.append({"type":1,"components":[button]})
            else:
                return payload,None
            return out,{"group":group,"avatar":picked,"banner":picked}
    return payload,None


def mark_delivered(selection):
    """Persist only after Discord accepts the alert, never during preview selection."""
    if not isinstance(selection,dict):return
    group=selection.get("group")
    item=selection.get("banner")
    if group not in ("hanayo","sakihane") or not item:return
    with LOCK:
        history=read(HISTORY)
        used=history.setdefault(group,{})
        used[item[2]]=datetime.now(timezone.utc).isoformat()
        write(HISTORY,history)
