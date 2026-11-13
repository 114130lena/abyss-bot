"""Discover images and video on an X status before choosing Discord footer media."""
import logging
from urllib.parse import urlsplit
import requests

import re

_SPACE_LINK = re.compile(
    r"https?://(?:www\.)?(?:x|twitter)\.com/i/spaces/([A-Za-z0-9]{8,32})(?=$|[/?#\s\]\)])",
    re.I)
_TCO_LINK = re.compile(r"(?:https?://[^/\s\]\)]+/)?t\.co/([A-Za-z0-9]{5,32})", re.I)
_MARKDOWN_URL = re.compile(r"\[\s*https?://[^\]\s]+\s*\]\(\s*https?://[^\s\)]+\s*\)")
_ANY_URL = re.compile(r"https?://[^\s]+")


def _space_url(value):
    """Canonicalize only explicitly identified X Space links."""
    match = _SPACE_LINK.search(str(value or ""))
    return "https://x.com/i/spaces/" + match.group(1) if match else ""


def _shortened_link_url(body):
    # Some Nitter mirrors turn https://t.co/CODE into
    # https://nitter.example/t.co/CODE inside an existing Markdown link.
    match = _TCO_LINK.search(str(body or ""))
    return "https://t.co/" + match.group(1) if match else ""


def _expand_space_shortlink(short_url):
    """Inspect a trusted t.co redirect without following arbitrary destinations."""
    if not re.fullmatch(r"https://t\.co/[A-Za-z0-9]{5,32}", str(short_url)):
        return ""
    try:
        response = requests.head(
            short_url, timeout=(1, 2), allow_redirects=False,
            headers={"User-Agent": "abyss-bot/1.0"})
        try:
            if 300 <= response.status_code < 400:
                return _space_url(response.headers.get("Location", ""))
            return ""
        finally:
            response.close()
    except (requests.RequestException, ValueError, TypeError):
        return ""


def describe_x_link_post(body):
    """Return a safe title/body for confirmed Spaces or ambiguous link-only posts.

    Space status is never inferred from the presence of t.co alone.
    """
    original = str(body or "").strip()
    if not original:
        return None
    space = _space_url(original)
    remaining = _MARKDOWN_URL.sub("", original)
    remaining = _ANY_URL.sub("", remaining).strip(" \t\r\n[]()<>")
    link_only = not remaining
    if not space and not link_only:
        return None
    if not space:
        short = _shortened_link_url(original)
        if short:
            space = _expand_space_shortlink(short)
    if space:
        return "🎙️ X 스페이스 공유", "🎙️ [스페이스 입장하기](" + space + ")"
    if link_only:
        return "🔗 X 링크 게시물", "링크의 내용을 확인하지 못했습니다. X 원본 게시물에서 확인해 주세요."
    return None


def _media_url(value):
    url=str(value or "")
    p=urlsplit(url)
    return url if p.scheme=="https" and p.hostname in ("pbs.twimg.com","video.twimg.com","pbs.twimg.com:443") else ""

def _playable_video_url(item):
    """Select a direct HTTPS MP4/WebM video suitable for Discord V2 galleries.

    HLS playlists (.m3u8) and still-image thumbnails are not playable videos.
    Trust only X's video CDN and a valid video file extension before embedding.
    """
    from urllib.parse import urlsplit
    candidates = []
    if not isinstance(item, dict):
        return ""
    for fmt in item.get("formats") or []:
        if not isinstance(fmt, dict):
            continue
        media_type = str(fmt.get("container") or fmt.get("content_type") or "").lower()
        u = fmt.get("url") or ""
        if media_type in ("mp4", "webm", "video/mp4", "video/webm") or urlsplit(str(u)).path.lower().endswith((".mp4", ".webm")):
            candidates.append((int(fmt.get("bitrate") or 0), u))
    for key in ("url", "video_url", "playback_url", "transcode_url"):
        if item.get(key):
            candidates.append((0, item[key]))
    for _, url in sorted(candidates, key=lambda candidate: candidate[0], reverse=True):
        parsed = urlsplit(str(url))
        if (parsed.scheme == "https" and parsed.hostname in ("video.twimg.com", "video.pscp.tv")
                and parsed.path.lower().endswith((".mp4", ".webm"))):
            return str(url)
    return ""


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
        # A secondary feed can have the photo even when FxTwitter exposes
        # an empty media block. Recover it instead of merely marking "unknown".
        direct_photos = []
        direct_videos = []
        for raw in media[:12]:
            if not isinstance(raw, str):
                continue
            parsed = urlsplit(raw)
            if parsed.scheme != "https":
                continue
            if parsed.hostname == "pbs.twimg.com" and parsed.path.startswith("/media/"):
                if raw not in direct_photos:
                    direct_photos.append(raw)
            elif parsed.hostname == "video.twimg.com" and parsed.path.lower().endswith((".mp4", ".webm")):
                direct_videos.append(raw)
        if direct_photos or direct_videos:
            return direct_photos[:4], direct_videos[0] if direct_videos else "", bool(direct_videos), True
        # An explicit mediaURLs list is stronger evidence than absent media.
        if (media or data.get("hasMedia") or data.get("media_extended")
                or data.get("combinedMediaUrl") or data.get("videoURL")
                or data.get("has_video") or data.get("video")):
            logging.info("X_MEDIA_RECHECK_UNVERIFIED username=%s post=%s reason=secondary_has_media", username, post_id)
            return None
        if not isinstance(data.get("text"), str) or not data["text"].strip():
            return None
        logging.info("X_MEDIA_TEXT_ONLY_VERIFIED username=%s post=%s via=vxtwitter", username, post_id)
        return ([], "", False, True)
    except (requests.RequestException, ValueError, TypeError) as exc:
        logging.info("X_MEDIA_RECHECK_FAILED username=%s post=%s reason=%s", username, post_id, type(exc).__name__)
    return None



def _vx_positive_media(username, post_id, existing):
    """Independent source for media when FxTwitter v2 is incomplete."""
    try:
        response = requests.get(
            f"https://api.vxtwitter.com/{username}/status/{post_id}",
            timeout=(1.5, 3), headers={"User-Agent": "abyss-bot/1.0"})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return None
        actual_id = str(data.get("tweetID") or data.get("id") or "")
        actual_user = str(data.get("user_screen_name") or data.get("screen_name") or "")
        if actual_id != post_id or actual_user.casefold() != username.lstrip("@").casefold():
            return None
        urls = data.get("mediaURLs")
        photos = list(existing)
        videos = []
        if isinstance(urls, list):
            for raw in urls[:12]:
                if not isinstance(raw, str):
                    continue
                parsed = urlsplit(raw)
                if parsed.scheme != "https":
                    continue
                if parsed.hostname == "pbs.twimg.com" and parsed.path.startswith("/media/"):
                    if raw not in photos:
                        photos.append(raw)
                elif parsed.hostname == "video.twimg.com" and parsed.path.lower().endswith((".mp4", ".webm")):
                    videos.append(raw)
        direct = data.get("videoURL") or data.get("combinedMediaUrl")
        if isinstance(direct, str):
            parsed = urlsplit(direct)
            if parsed.scheme == "https" and parsed.hostname == "video.twimg.com" and parsed.path.lower().endswith((".mp4", ".webm")):
                if direct not in videos:
                    videos.append(direct)
        poster = ""
        for value in (data.get("media_extended") or []):
            if not isinstance(value, dict):
                continue
            candidate = _media_url(value.get("thumbnail_url") or value.get("thumbnail"))
            if candidate and urlsplit(candidate).hostname == "pbs.twimg.com":
                poster = candidate
                break
        if len(photos) > len(existing) or videos or poster:
            logging.info("X_MEDIA_VX_RECOVERED username=%s post=%s photos=%d videos=%d",
                         username, post_id, len(photos), len(videos))
            return photos[:4], videos[0] if videos else poster, bool(videos), True
    except (requests.RequestException, ValueError, TypeError) as exc:
        logging.info("X_MEDIA_VX_UNAVAILABLE post=%s reason=%s", post_id, type(exc).__name__)
    return None


def _recover_x_media(post, username, post_id, existing):
    if existing:
        # Avoid another API call in the critical notification path.
        return existing[:4], "", False, False
    fallback = _vx_positive_media(username, post_id, existing)
    if fallback is not None:
        return fallback
    return existing, "", False, False


def resolve_media(post, username):
    """Return (photos, video_preview, has_video, known).

    An unreachable/ambiguous API response is unknown, never a confirmed text-only post.
    Prefer a direct MP4/WebM URL in Discord media galleries when available,
    falling back to the video thumbnail and original X link when absent.
    """
    existing=list(post.get("images") or [])
    # Bluebird's authenticated X response already contains the actual media
    # URLs. Trust positive media witnesses and avoid a slower public API lookup.
    # For repost wrappers, NEVER infer that an empty wrapper means an empty
    # original tweet; the caller may resolve the original separately.
    if post.get("source") == "bluebird-twscraper":
        candidate = str(post.get("bluebird_video_url") or "")
        playback = _playable_video_url({"url": candidate}) if candidate else ""
        photos = []
        for value in existing[:4]:
            url = _media_url(value)
            if url and urlsplit(url).hostname == "pbs.twimg.com" and "/media/" in urlsplit(url).path:
                if url not in photos:
                    photos.append(url)
        if photos or playback:
            return photos, playback, bool(playback), True
        if post.get("bluebird_media_verified") is True and post.get("repost") is not True:
            return [], "", False, True
    post_id=str(post.get("id",""))
    if not post_id.isdigit():
        return existing,"",False,bool(existing)
    try:
        endpoint=f"https://api.fxtwitter.com/2/status/{post_id}"
        media=None
        tweet={}
        # One bounded enrichment attempt. RSS photos remain as fallback;
        # repeated upstream waits must not block Discord delivery.
        response=requests.get(endpoint,timeout=(1.5,3),headers={"User-Agent":"abyss-bot/1.0"})
        response.raise_for_status()
        data=response.json()
        if not isinstance(data,dict) or data.get("code", 200) != 200:
            return _recover_x_media(post, username, post_id, existing)
        candidate=data.get("status") or data.get("tweet")
        if not isinstance(candidate,dict):
            return _recover_x_media(post, username, post_id, existing)
        tweet=candidate
        # Check the returned post, not merely the requested URL. Wrong-account
        # metadata is worse than temporarily missing media.
        actual_id = str(tweet.get("id") or tweet.get("id_str") or "")
        if actual_id and actual_id != post_id:
            logging.warning("X_MEDIA_ID_MISMATCH expected=%s actual=%s", post_id, actual_id)
            return _recover_x_media(post, username, post_id, existing)
        author = tweet.get("author") or {}
        actual_handle = (author.get("screen_name") or author.get("username") or "") if isinstance(author,dict) else ""
        if actual_handle and actual_handle.casefold() != username.lstrip("@").casefold():
            logging.warning("X_MEDIA_AUTHOR_MISMATCH expected=%s", username)
            return _recover_x_media(post, username, post_id, existing)
        media=tweet.get("media")
        if media is None or media == {}:
            if existing:
                return existing[:4], "", False, False
            if _complete_text_only(tweet, username, post_id):
                verified = _recheck_empty(post, username, post_id, tweet)
                if verified is not None:
                    return verified
                # The secondary service has already been queried once.
                return existing, "", False, False
            return _recover_x_media(post, username, post_id, existing)
        if not isinstance(media,dict):
            logging.warning("X_MEDIA_EMPTY_UNVERIFIED username=%s post=%s",username,post_id)
            return _recover_x_media(post, username, post_id, existing)
        photos=[]
        video_items=[]
        # FxTwitter v1/v2 may report GIFs under photos, videos or media.all.
        for item in media.get("photos") or []:
            if not isinstance(item,dict): continue
            if item.get("type") == "gif":
                video_items.append(item)
                continue
            url=_media_url(item.get("url") or item.get("media_url_https"))
            if url and url not in photos: photos.append(url)
        for key in ("videos", "animated_gifs", "gifs"):
            for item in media.get(key) or []:
                if isinstance(item,dict):
                    video_items.append(item)
        if not photos and not video_items:
            for item in media.get("all") or []:
                if not isinstance(item,dict): continue
                if item.get("type") in ("video", "gif"):
                    video_items.append(item)
                elif item.get("type") == "photo":
                    url=_media_url(item.get("url"))
                    if url and url not in photos: photos.append(url)
        if not photos: photos=existing
        has_video=bool(video_items)
        preview=""
        for v in video_items:
            if not isinstance(v,dict):continue
            # A playable video takes precedence over the poster thumbnail.
            preview=_playable_video_url(v)
            if preview:break
        if has_video and not preview:
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
        confirmed=bool(photos) or has_video
        if not confirmed:
            return _recover_x_media(post, username, post_id, existing)
        return photos[:4],preview,has_video,confirmed
    except (requests.RequestException,ValueError,TypeError) as exc:
        logging.warning("X_MEDIA_UNKNOWN username=%s post=%s reason=%s",username,post_id,str(exc)[:120])
        return _recover_x_media(post, username, post_id, existing)

def resolve_images(post,username):
    """Backward-compatible photo lookup for other consumers."""
    photos,_,_,known=resolve_media(post,username)
    return photos,known
