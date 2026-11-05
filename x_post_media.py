"""Discover images and video on an X status before choosing Discord footer media."""
import logging
import time
from urllib.parse import urlsplit
import requests

def _media_url(value):
    url=str(value or "")
    p=urlsplit(url)
    return url if p.scheme=="https" and p.hostname in ("pbs.twimg.com","video.twimg.com","pbs.twimg.com:443") else ""

def _direct_video_url(value):
    """Only use original public MP4 links, never arbitrary third-party media."""
    url = str(value or "")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "video.twimg.com"
            or parsed.username or parsed.password or len(url) > 2048
            or not parsed.path.lower().endswith(".mp4")):
        return ""
    return url


def _resolve_media_all(post, username):
    """Return (photos, video_preview, has_video, known, direct_mp4).

    An unreachable/ambiguous API response is unknown, never a confirmed text-only post.
    Preserve the video thumbnail and original X link as fallback if an\n    external MP4 cannot be embedded by the Discord client.
    """
    existing=list(post.get("images") or [])
    post_id=str(post.get("id",""))
    if not post_id.isdigit():
        return existing,"",False,bool(existing),""
    try:
        endpoint=f"https://api.fxtwitter.com/{username}/status/{post_id}"
        media=None
        tweet={}
        for attempt in range(3):
            response=requests.get(endpoint,timeout=(2.5,4),headers={"User-Agent":"abyss-bot/1.0"})
            response.raise_for_status()
            data=response.json()
            candidate=data.get("tweet") if isinstance(data,dict) else None
            if not isinstance(candidate,dict):
                return existing,"",False,False,""
            tweet=candidate
            media=tweet.get("media")
            if isinstance(media,dict):
                break
            # Missing metadata is not proof the original post is text-only.
            if attempt<2:
                time.sleep(2)
        if media is None:
            logging.warning("X_MEDIA_UNVERIFIED_EMPTY username=%s post=%s",username,post_id)
            return existing,"",False,False,""
        if not isinstance(media,dict) or not media:
            logging.warning("X_MEDIA_EMPTY_UNVERIFIED username=%s post=%s",username,post_id)
            return existing,"",False,False,""
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
        direct_mp4=""
        for v in video_items:
            if not isinstance(v,dict):continue
            if not direct_mp4:
                direct_mp4 = _direct_video_url(v.get("url") or v.get("src"))
                for variant in (v.get("variants") or []):
                    if direct_mp4 or not isinstance(variant, dict):
                        break
                    direct_mp4 = _direct_video_url(variant.get("url"))
            preview=_media_url(v.get("thumbnail_url") or v.get("thumbnail") or v.get("poster") or v.get("preview_image_url"))
            if preview:break
        # Some FxTwitter versions place a card or animated clip outside media.videos.
        if not has_video and (tweet.get("has_video") is True or tweet.get("video") or media.get("video")):
            has_video=True
        if has_video and not preview:
            preview=_media_url(media.get("thumbnail_url") or tweet.get("thumbnail_url"))
        logging.info("X_MEDIA_CLASSIFIED username=%s post=%s photos=%d video=%s preview=%s",
                     username,post_id,len(photos),has_video,bool(preview))
        # Require an explicit empty media list before claiming a text-only post.
        confirmed=(bool(photos) or has_video or
                   ("all" in media and isinstance(media["all"],list) and len(media["all"])==0))
        return photos[:4],preview,has_video,confirmed,direct_mp4
    except (requests.RequestException,ValueError,TypeError) as exc:
        logging.warning("X_MEDIA_UNKNOWN username=%s post=%s reason=%s",username,post_id,str(exc)[:120])
        return existing,"",False,False,""

def resolve_media(post, username):
    """Compatibility API for existing photo-safety callers."""
    return _resolve_media_all(post, username)[:4]


def resolve_media_with_video(post, username):
    """Video-capable variant for Discord native inline MP4 galleries."""
    return _resolve_media_all(post, username)


def resolve_images(post,username):
    """Backward-compatible photo lookup for other consumers."""
    photos,_,_,known=resolve_media(post,username)
    return photos,known
