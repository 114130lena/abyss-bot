"""Check authoritative per-post media metadata before choosing fanart.

An inconclusive API response must not be mistaken for a confirmed text-only post.
"""
import logging
from urllib.parse import urlsplit
import requests

def resolve_images(post, username):
    existing=list(post.get("images") or [])
    if existing:
        return existing, True
    post_id=str(post.get("id",""))
    if not post_id.isdigit():
        return [], False
    endpoint=f"https://api.fxtwitter.com/{username}/status/{post_id}"
    try:
        response=requests.get(endpoint,timeout=3,headers={"User-Agent":"abyss-bot/1.0"})
        response.raise_for_status()
        data=response.json()
        tweet=data.get("tweet") if isinstance(data,dict) else None
        if not isinstance(tweet,dict):
            return [],False
        media=tweet.get("media")
        if media is None:
            logging.info("X_PHOTO_CONFIRMED_NONE user=%s id=%s",username,post_id)
            return [],True
        if not isinstance(media,dict):
            return [],False
        urls=[]
        for kind in ("photos",):
            for item in media.get(kind,[]) or []:
                if not isinstance(item,dict):continue
                url=item.get("url") or item.get("media_url_https") or ""
                parsed=urlsplit(str(url))
                if parsed.scheme=="https" and parsed.hostname=="pbs.twimg.com" and "/media/" in parsed.path and url not in urls:
                    urls.append(url)
        logging.info("X_PHOTO_CHECK user=%s id=%s photos=%d",username,post_id,len(urls))
        return urls[:4],True
    except (requests.RequestException,ValueError,TypeError) as exc:
        logging.warning("X_PHOTO_UNKNOWN user=%s id=%s reason=%s",username,post_id,str(exc)[:120])
        return [],False
