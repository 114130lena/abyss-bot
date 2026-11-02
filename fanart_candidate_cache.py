"""Cache discovered hashtag art media for review without auto-republishing.

Only HTTPS pbs.twimg.com media; images are stored under fanart_candidates
and never referenced by the public random banner pool.
"""
import hashlib,io,json,logging,os
from pathlib import Path
from urllib.parse import urlsplit
import requests
from PIL import Image,ImageOps
from fanart_discovery import BASE,QUEUE,read,write
CACHE=BASE/"fanart_candidates"
MAX_BYTES=12*1024*1024
MAX_PER_RUN=75

def retrieve(session,url):
    p=urlsplit(str(url))
    if p.scheme!="https" or p.hostname!="pbs.twimg.com" or not p.path.startswith("/media/"):
        raise ValueError("Not a verified X image URL")
    with session.get(url,timeout=18,stream=True,allow_redirects=False) as response:
        response.raise_for_status()
        if not response.headers.get("Content-Type","").lower().startswith("image/"):
            raise ValueError("Not an image")
        chunks=bytearray()
        for part in response.iter_content(65536):
            chunks.extend(part)
            if len(chunks)>MAX_BYTES:raise ValueError("Oversize image")
    raw=bytes(chunks)
    with Image.open(io.BytesIO(raw)) as image:
        image.verify()
    with Image.open(io.BytesIO(raw)) as image:
        if image.width<160 or image.height<160 or image.width>8192 or image.height>8192:
            raise ValueError("Image dimensions invalid")
        image=ImageOps.exif_transpose(image)
        dest=io.BytesIO()
        image.convert("RGB").save(dest,format="JPEG",quality=93)
        data=dest.getvalue()
    return data,hashlib.sha256(data).hexdigest()

def run(session=None):
    session=session or requests.Session()
    queue=read(QUEUE)
    stored=0;failed=0;changed=False
    for key,item in queue.items():
        if stored>=MAX_PER_RUN:break
        if not isinstance(item,dict) or item.get("review_status")=="blocked" or item.get("cache_retired"):continue
        if item.get("group") not in ("hanayo","sakihane"):continue
        existing=item.get("cached_files",[])
        if existing and all((BASE/p).is_file() for p in existing if isinstance(p,str)):
            continue
        files=[]
        for url in item.get("image_urls",[])[:4]:
            try:
                data,digest=retrieve(session,url)
                relative=Path("fanart_candidates")/item["group"]/(digest+".jpg")
                destination=BASE/relative
                destination.parent.mkdir(parents=True,exist_ok=True)
                if not destination.is_file():
                    temp=destination.with_suffix(".tmp")
                    temp.write_bytes(data)
                    temp.replace(destination)
                files.append(str(relative))
                stored+=1
            except (ValueError,OSError,requests.RequestException) as exc:
                failed+=1
                logging.warning("Fanart image download failed for %s: %s",key,type(exc).__name__)
        if files:
            item["cached_files"]=files
            changed=True
    if changed:write(QUEUE,queue)
    result={"cached":stored,"failed":failed,"publicly_approved":0}
    print(json.dumps(result,ensure_ascii=False))
    return result

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO)
    run()
