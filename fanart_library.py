"""Curated fan-art submissions for #hiyoriart_ and #huyart.

X does not grant permission for automated hashtag scraping. This module imports
creator-approved, voluntarily submitted image files and metadata; no X requests.
Accepted entries are isolated from the official random pool until approved.
"""
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from PIL import Image

BASE=Path(os.getenv("BOT_DATA_DIR","/home/ubuntu/chzzk-bot"))/"creator_media"
INCOMING=BASE/"fanart_inbox"
INDEX=BASE/"fanart_index.json"
TAGS={"hanayo":"hiyoriart_","sakihane":"huyart"}
MAX_SIZE=12*1024*1024

def _read():
    try:
        value=json.loads(INDEX.read_text("utf-8"))
        return value if isinstance(value,dict) else {}
    except (ValueError,OSError):
        return {}

def _save(value):
    INDEX.parent.mkdir(parents=True,exist_ok=True)
    temp=INDEX.with_suffix(".tmp")
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    temp.replace(INDEX)

def _valid_post(group,url):
    if not isinstance(url,str): return False
    p=urlsplit(url)
    if p.scheme!="https" or p.hostname not in ("x.com","twitter.com","www.x.com"):
        return False
    return bool(re.fullmatch(r"/[A-Za-z0-9_]{1,15}/status/\d+",p.path))


def make_banner(source, destination, width=1280, height=720):
    """Fit the full image to a 16:9 card without cropping; blur background only."""
    from PIL import ImageOps, ImageFilter
    with Image.open(source) as original:
        image=ImageOps.exif_transpose(original).convert("RGB")
        fitted=ImageOps.contain(image,(width,height),Image.Resampling.LANCZOS)
        background=ImageOps.fit(image,(width,height),method=Image.Resampling.LANCZOS)
        background=background.filter(ImageFilter.GaussianBlur(36))
        background=Image.blend(background,Image.new("RGB",(width,height),(21,23,32)),0.52)
        background.paste(fitted,((width-fitted.width)//2,(height-fitted.height)//2))
        destination.parent.mkdir(parents=True,exist_ok=True)
        background.save(destination,format="JPEG",quality=90,optimize=True)

def ingest():
    """Only accept creator-consented submissions with verified exact hashtag.

    Place image.jpg alongside image.json in fanart_inbox. Sidecar requirements:
    group, hashtag, post_url, artist, permission_confirmed=true, status=approved.
    """
    output=_read()
    counts={"imported":0,"rejected":0,"pending":0}
    for metadata in INCOMING.glob("*.json"):
        try:
            item=json.loads(metadata.read_text("utf-8"))
            group=item.get("group")
            hashtag=str(item.get("hashtag","")).lstrip("#").lower()
            if group not in TAGS or hashtag!=TAGS[group]:
                raise ValueError("Wrong hashtag/group")
            if not _valid_post(group,item.get("post_url")):
                raise ValueError("Invalid original X post")
            if not re.fullmatch(r"@[A-Za-z0-9_]{1,15}",str(item.get("artist",""))):
                raise ValueError("Missing artist credit")
            if item.get("permission_confirmed") is not True:
                counts["pending"]+=1
                continue
            if item.get("status")!="approved":
                counts["pending"]+=1
                continue
            files=[p for p in (metadata.with_suffix(".png"),metadata.with_suffix(".jpg"),metadata.with_suffix(".jpeg"),metadata.with_suffix(".webp")) if p.is_file()]
            if len(files)!=1: raise ValueError("Image not found or ambiguous")
            source=files[0]
            if not 0<source.stat().st_size<=MAX_SIZE: raise ValueError("Image size")
            with Image.open(source) as im:
                im.verify()
            with Image.open(source) as im:
                w,h=im.size
                if not 160<=w<=8000 or not 160<=h<=8000:
                    raise ValueError("Dimensions")
            raw=source.read_bytes()
            digest=hashlib.sha256(raw).hexdigest()
            target=BASE/"fanart"/group/(digest+source.suffix.lower())
            target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists(): shutil.copyfile(source,target)
            banner=BASE/'fanart_banners'/group/(digest+'.jpg')
            if not banner.exists(): make_banner(target,banner)
            entries=output.setdefault(group,[])
            if not any(x.get("sha256")==digest for x in entries):
                entries.append({"sha256":digest,"path":str(banner.relative_to(BASE)),
                    "original_path":str(target.relative_to(BASE)),
                    "post_url":item["post_url"],"artist":item["artist"],
                    "hashtag":"#"+TAGS[group],"approved":True,
                    "permission_confirmed":True,"width":w,"height":h,
                    "added_at":datetime.now(timezone.utc).isoformat()})
                counts["imported"]+=1
        except (ValueError,OSError,TypeError,KeyError) as exc:
            counts["rejected"]+=1
            print("FANART_REJECTED",metadata.name,str(exc))
    _save(output)
    print(json.dumps(counts,ensure_ascii=False))
    return counts

if __name__=="__main__":
    ingest()
