"""Select public X media URLs for Discord V2 cards without re-uploading image bytes."""
import copy,json,logging,os,random,threading
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
            source=item.get("post_url","")
            if _usable(url) and isinstance(source,str) and source.startswith("https://x.com/"):
                result.append((url,item.get("artist",""),source))
    return result

def has_post_photos(payload):
    containers=[c for c in payload.get("components",[]) if c.get("type")==17]
    if not containers:return False
    parts=containers[-1].get("components",[])
    tag=" ".join(str(c.get("content","")) for c in parts if c.get("type")==10)
    if not ("𝕏 X" in tag or "NAVER CAFE" in tag):
        return False
    footer=next((i for i,c in enumerate(parts) if c.get("type")==10 and
                 "OFFICIAL LINKS" in c.get("content","")),len(parts))
    return any(c.get("type")==12 and c.get("items") for c in parts[:footer])


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
    # YouTube uploads and CHZZK replays use the official YouTube banner.
    tag=" ".join(str(c.get("content","")) for c in parts if c.get("type")==10)
    if "YOUTUBE" in tag or ("CHZZK" in tag and "다시보기" in tag):
        return payload,None
    # Only X/Cafe post attachments suppress fanart. YouTube thumbnail galleries
    # are not post-body photos and CHZZK replay art is always eligible.
    if has_post_photos(payload):
        return payload,None
    pool=candidates(group)
    if not pool:
        logging.warning("FANART_FALLBACK group=%s reason=no_eligible_candidates",group)
        return out,None
    # Unused artworks first; after exhaustion recycle the least recently used.
    # Preserve history across restarts, and never fall back merely because all were shown.
    with LOCK:
        used=read(HISTORY).get(group,{})
        if not isinstance(used,dict):used={}
        fresh=[x for x in pool if x[2] not in used]
        if fresh:
            picked=random.SystemRandom().choice(fresh)
            cycle="fresh"
        else:
            oldest=min(str(used.get(x[2],"")) for x in pool)
            picked=random.SystemRandom().choice([x for x in pool if str(used.get(x[2],""))==oldest])
            cycle="recycled"
    logging.info("FANART_PICK group=%s mode=%s available=%d unused=%d",group,cycle,len(pool),len(fresh))
    # Preserve the independently selected official/random creator profile.
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
            # This is a fifth *new* button. Never overwrite one of the four
            # permanent platform links or silently trim the last button.
            required=("CHZZK","CAFE","X","YouTube")
            if not row:
                logging.warning("FANART_FALLBACK group=%s reason=missing_button_row",group)
                return payload,None
            base_buttons=[x for x in row["components"] if x.get("label")!="원본 게시물"]
            if tuple(x.get("label") for x in base_buttons)!=required:
                logging.warning("FANART_FALLBACK group=%s reason=footer_buttons_invalid",group)
                return payload,None
            row["components"]=[button]+base_buttons
            assert len(row["components"])==5
            return out,{"group":group,"avatar":picked,"banner":picked}
    logging.warning("FANART_FALLBACK group=%s reason=no_footer_gallery",group)
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
