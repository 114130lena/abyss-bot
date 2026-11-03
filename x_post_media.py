"""Discover images and video on an X status before choosing Discord footer media."""
import logging
from urllib.parse import urlsplit
import requests

def _media_url(value):
    url=str(value or "")
    p=urlsplit(url)
    return url if p.scheme=="https" and p.hostname in ("pbs.twimg.com","video.twimg.com","pbs.twimg.com:443") else ""

def resolve_media(post, username):
    """Return (photos, video_preview, has_video, known).

    An unreachable/ambiguous API response is unknown, never a confirmed text-only post.
    Prefer a public video thumbnail with a clickable original X post link:
    remote MP4 URLs cannot be assumed to embed correctly in Discord.
    """
    existing=list(post.get("images") or [])
    post_id=str(post.get("id",""))
    if not post_id.isdigit():
        return existing,"",False,bool(existing)
    try:
        endpoint=f"https://api.fxtwitter.com/{username}/status/{post_id}"
        response=requests.get(endpoint,timeout=4,headers={"User-Agent":"abyss-bot/1.0"})
        response.raise_for_status()
        data=response.json()
        tweet=data.get("tweet") if isinstance(data,dict) else None
        if not isinstance(tweet,dict):
            return existing,"",False,False
        media=tweet.get("media")
        if media is None:
            # Some successful replies lack media object; do not suppress RSS attachments.
            return existing,"",False,True
        if not isinstance(media,dict):
            return existing,"",False,False
        photos=[]
        for item in media.get("photos",[]) or []:
            if not isinstance(item,dict):continue
            url=_media_url(item.get("url") or item.get("media_url_https"))
            if url and url not in photos: photos.append(url)
        if not photos:photos=existing
        video_items=list(media.get("videos") or [])
        video_items+=list(media.get("animated_gifs") or [])
        video_items+=list(media.get("gifs") or [])
        has_video=bool(video_items)
        preview=""
        for v in video_items:
            if not isinstance(v,dict):continue
            preview=_media_url(v.get("thumbnail_url") or v.get("thumbnail") or v.get("poster") or v.get("preview_image_url"))
            if preview:break
        # Some FxTwitter versions place a card or animated clip outside media.videos.
        if not has_video and (tweet.get("has_video") is True or tweet.get("video") or media.get("video")):
            has_video=True
        if has_video and not preview:
            preview=_media_url(media.get("thumbnail_url") or tweet.get("thumbnail_url"))
        logging.info("X_MEDIA_CLASSIFIED username=%s post=%s photos=%d video=%s preview=%s",
                     username,post_id,len(photos),has_video,bool(preview))
        return photos[:4],preview,has_video,True
    except (requests.RequestException,ValueError,TypeError) as exc:
        logging.warning("X_MEDIA_UNKNOWN username=%s post=%s reason=%s",username,post_id,str(exc)[:120])
        return existing,"",False,False

def resolve_images(post,username):
    """Backward-compatible photo lookup for other consumers."""
    photos,_,_,known=resolve_media(post,username)
    return photos,known
