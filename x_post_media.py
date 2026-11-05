"""Discover images and video on an X status before choosing Discord footer media."""
import logging
from urllib.parse import urlsplit
import requests

def _media_url(value):
    url=str(value or "")
    p=urlsplit(url)
    return url if p.scheme=="https" and p.hostname in ("pbs.twimg.com","video.twimg.com","pbs.twimg.com:443") else ""

def _complete_text_only(tweet, username, post_id):
    """A complete matching tweet without media indicators is a text-only witness.

    Missing/empty media by itself is NOT proof; require a matching status ID,
    owner and actual text before counting a response as a witness.
    """
    if not isinstance(tweet, dict):
        return False
    if str(tweet.get("id") or tweet.get("id_str") or "") != post_id:
        return False
    author = tweet.get("author") or {}
    screen_name = (author.get("screen_name") or author.get("username") or "") if isinstance(author, dict) else ""
    if not screen_name or screen_name.casefold() != username.lstrip("@").casefold():
        return False
    if not isinstance(tweet.get("text"), str) or not tweet["text"].strip():
        return False
    media = tweet.get("media")
    if media is not None and media != {} and media != []:
        return False
    if any(tweet.get(flag) for flag in ("has_video", "video", "extended_entities", "card")):
        return False
    return True


def _recheck_empty(post, username, post_id, primary_tweet):
    """One bounded second-source verification before allowing notification fanart."""
    if post.get("images") or not _complete_text_only(primary_tweet, username, post_id):
        logging.info("X_MEDIA_RECHECK_UNVERIFIED username=%s post=%s reason=incomplete_primary", username, post_id)
        return None
    try:
        endpoint = f"https://api.vxtwitter.com/{username}/status/{post_id}"
        response = requests.get(endpoint, timeout=(1.5, 3), headers={"User-Agent": "abyss-bot/1.0"})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return None
        # VX replies with a flat tweet object. Reject error envelopes, redirects
        # or a tweet belonging to a different status/account.
        if str(data.get("tweetID") or data.get("id") or "") != post_id:
            return None
        user = data.get("user_screen_name") or data.get("screen_name") or ""
        if user.casefold() != username.lstrip("@").casefold():
            return None
        media = data.get("mediaURLs")
        if not isinstance(media, list):
            return None
        # An explicit mediaURLs list is stronger evidence than absent media.
        if media or data.get("has_video") or data.get("videoURL") or data.get("video"):
            logging.info("X_MEDIA_RECHECK_UNVERIFIED username=%s post=%s reason=secondary_has_media", username, post_id)
            return None
        if not isinstance(data.get("text"), str) or not data["text"].strip():
            return None
        logging.info("X_MEDIA_TEXT_ONLY_VERIFIED username=%s post=%s via=vxtwitter", username, post_id)
        return ([], "", False, True)
    except (requests.RequestException, ValueError, TypeError) as exc:
        logging.info("X_MEDIA_RECHECK_FAILED username=%s post=%s reason=%s", username, post_id, type(exc).__name__)
    return None


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
        media=None
        tweet={}
        # One bounded enrichment attempt. RSS photos remain as fallback;
        # repeated upstream waits must not block Discord delivery.
        response=requests.get(endpoint,timeout=(1.5,3),headers={"User-Agent":"abyss-bot/1.0"})
        response.raise_for_status()
        data=response.json()
        candidate=data.get("tweet") if isinstance(data,dict) else None
        if not isinstance(candidate,dict):
            return existing,"",False,False
        tweet=candidate
        media=tweet.get("media")
        if media is None or media == {}:
            verified = _recheck_empty(post, username, post_id, tweet)
            if verified is not None:
                return verified
            logging.warning("X_MEDIA_UNVERIFIED_EMPTY username=%s post=%s",username,post_id)
            return existing,"",False,False
        if not isinstance(media,dict):
            logging.warning("X_MEDIA_EMPTY_UNVERIFIED username=%s post=%s",username,post_id)
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
        # Require an explicit empty media list before claiming a text-only post.
        confirmed=(bool(photos) or has_video or
                   ("all" in media and isinstance(media["all"],list) and len(media["all"])==0))
        return photos[:4],preview,has_video,confirmed
    except (requests.RequestException,ValueError,TypeError) as exc:
        logging.warning("X_MEDIA_UNKNOWN username=%s post=%s reason=%s",username,post_id,str(exc)[:120])
        return existing,"",False,False

def resolve_images(post,username):
    """Backward-compatible photo lookup for other consumers."""
    photos,_,_,known=resolve_media(post,username)
    return photos,known
