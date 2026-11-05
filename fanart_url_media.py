"""Select public X media URLs for Discord V2 cards without re-uploading image bytes."""
import copy,json,logging,os,random,threading
from datetime import datetime,timezone
from urllib.parse import urlsplit
from pathlib import Path
from hashlib import sha256
from fanart_discovery import BASE, QUEUE, read, write
from fanart_usage import notification_allowed

HISTORY=BASE/'fanart_url_used.json'
LOCK=threading.RLock()


def _usable(url):
    p=urlsplit(str(url or ""))
    return p.scheme=="https" and p.hostname=="pbs.twimg.com" and p.path.startswith("/media/")

def candidates(group):
    """Choose entire posts, not one randomly selected image from a multi-art post.

    Only source images from the *same* X status can share a footer gallery.
    Preserve the media order that X supplied; remove duplicated photo URLs.
    """
    posts = {}
    for item in read(QUEUE).values():
        if (not isinstance(item, dict) or item.get("group") != group
                or item.get("review_status") != "approved"
                or item.get("permission_confirmed") is not True
                or not notification_allowed(group, item)):
            continue
        source = item.get("post_url", "")
        if not isinstance(source, str) or not source.startswith("https://x.com/"):
            continue
        images = [url for url in item.get("image_urls", [])[:4] if _usable(url)]
        if not images:
            continue
        post = posts.setdefault(source, {"post_url": source, "artist": item.get("artist", ""),
                                        "images": []})
        for url in images:
            if url not in post["images"] and len(post["images"]) < 4:
                post["images"].append(url)
    # Restore previously approved local artwork even when it never entered the
    # newer X discovery review queue. Local entries have no public media URL.
    # Their curated banner JPEG is attached to Discord in send_webhook.
    library = read(BASE / "fanart_index.json")
    entries = library.get(group, []) if isinstance(library, dict) else []
    root = BASE.resolve()
    for item in entries if isinstance(entries, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("approved") is not True or item.get("permission_confirmed") is not True:
            continue
        if not notification_allowed(group, item):
            continue
        source = item.get("post_url", "")
        if not isinstance(source, str) or not source.startswith("https://x.com/"):
            continue
        if source in posts:
            continue
        relative = item.get("path")
        if not isinstance(relative, str) or not relative.startswith("fanart_banners/"):
            continue
        path = (BASE / relative).resolve()
        if root not in path.parents or not path.is_file():
            continue
        if path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        if not 0 < path.stat().st_size <= 8_000_000:
            continue
        # The path is local and will be uploaded as a Discord attachment.
        posts[source] = {"post_url": source, "artist": item.get("artist", ""),
                         "images": ["attachment://abyss_notification_fanart" + path.suffix.lower()],
                         "local_path": str(path)}
    return list(posts.values())


def has_post_photos(payload):
    """Detect post-specific media, never the footer banner or creator avatar.

    Inspect the original Discord card, before any randomization changes it.
    """
    import re
    containers=[c for c in payload.get("components",[]) if c.get("type")==17]
    if not containers:return False
    parts=containers[-1].get("components",[])
    tag=" ".join(str(c.get("content","")) for c in parts if c.get("type")==10)
    if not ("𝕏 X" in tag or "NAVER CAFE" in tag):
        return False
    # Locate the footer by its actual gallery rather than depending only
    # on a mutable OFFICIAL LINKS caption.
    footer=next((i for i,c in enumerate(parts)
                 if c.get("type")==10 and ("OFFICIAL LINKS" in str(c.get("content",""))
                   or "FAN ART" in str(c.get("content","")))),len(parts))
    for c in parts[:footer]:
        if c.get("type")==12 and c.get("items"):
            return True
        if c.get("type")==10:
            content=str(c.get("content",""))
            # RSS sometimes puts photos inline in the body rather than the
            # image gallery, e.g. Markdown photos or direct X CDN URLs.
            if re.search(r"https?://(?:pbs[.]twimg[.]com/media/|[^ )]+[.](?:png|jpe?g|webp)(?:[? )]|$))",content,re.I):
                return True
    return False


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
        logging.warning("FANART_FALLBACK group=%s reason=no_eligible_notification_candidates",group)
        return out,None
    # Unused artworks first; after exhaustion recycle the least recently used.
    # Preserve history across restarts, and never fall back merely because all were shown.
    with LOCK:
        used=read(HISTORY).get(group,{})
        if not isinstance(used,dict):used={}
        fresh = [x for x in pool if x["post_url"] not in used]
        if fresh:
            picked = random.SystemRandom().choice(fresh)
            cycle = "fresh"
        else:
            oldest = min(str(used.get(x["post_url"], "")) for x in pool)
            picked = random.SystemRandom().choice(
                [x for x in pool if str(used.get(x["post_url"], "")) == oldest])
            cycle = "recycled"
    logging.info("FANART_PICK group=%s mode=%s available=%d unused=%d",group,cycle,len(pool),len(fresh))
    # Preserve the independently selected official/random creator profile.
    for i,part in enumerate(parts[:-1]):
        if part.get("type")==10 and "OFFICIAL LINKS" in part.get("content",""):
            gallery=parts[i+1]
            if gallery.get("type")!=12:
                continue
            gallery["items"] = [
                {"media": {"url": image}, "description": "팬아트 %d/%d · %s" % (
                    index + 1, len(picked["images"]), picked["artist"])}
                for index, image in enumerate(picked["images"])
            ]
            # Show the entire selected post as one set; credit it once.
            part["content"] = "-# 🎨 FAN ART · " + str(picked["artist"]) + " · " + str(len(picked["images"])) + "장"
            source = picked["post_url"]
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
            selection = {"group":group,"post_url":source,
                         "banner":(picked["images"][0], picked["artist"], source)}
            if picked.get("local_path"):
                selection["local_path"] = picked["local_path"]
            return out, selection
    logging.warning("FANART_FALLBACK group=%s reason=no_footer_gallery",group)
    return payload,None


def mark_delivered(selection):
    """Persist only after Discord accepts the alert, never during preview selection."""
    if not isinstance(selection,dict):return
    group=selection.get("group")
    source = selection.get("post_url")
    if not source and selection.get("banner"):
        source = selection["banner"][2]
    if group not in ("hanayo","sakihane") or not isinstance(source,str) or not source:return
    with LOCK:
        history=read(HISTORY)
        used=history.setdefault(group,{})
        used[source]=datetime.now(timezone.utc).isoformat()
        write(HISTORY,history)
