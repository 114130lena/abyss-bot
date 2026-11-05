"""X retweet/repost notifications: no mentions, dedicated Discord thread."""
import os
import logging
import time
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from discord_v2 import alert
from resilience import send_webhook
from banner_manager import get_banner
from creator_profiles import handle,avatar

THREAD_ID="1566509366591627344"

def is_repost(title, description="", source=""):
    """Recognize repost labels used by Nitter, FxEmbed and RSS mirrors."""
    import re
    t=str(title or "").strip()
    d=str(description or "")
    if re.search(r"^(?:RT by|Retweeted by|Reposted by|Boosted by)\b", t, re.I):
        return True
    if re.search(r"^(?:RT|Retweet|Repost)\s*[:：]", t, re.I):
        return True
    if re.search(r"\b(?:retweeted|reposted)\s+by\b", t[:180], re.I):
        return True
    if re.search(r"\b(?:retweeted|reposted)\s+by\b", d[:250], re.I):
        return True
    return False


def is_repost_post(post, username):
    """Classify before routing, including off-author FxTwitter status links."""
    from urllib.parse import urlsplit
    import re
    if post.get("repost") is True:
        return True
    if is_repost(post.get("rss_title", ""), post.get("rss_description", "")):
        return True
    for key in ("url", "source_link"):
        parsed = urlsplit(str(post.get(key) or ""))
        match = re.fullmatch(r"/([^/]+)/status/([0-9]+)", parsed.path)
        if parsed.hostname in ("x.com", "twitter.com", "www.x.com", "www.twitter.com") and match:
            if match.group(1).casefold() != username.lstrip("@").casefold():
                return True
    return False


def thread_url(webhook):
    p=urlsplit(webhook)
    if p.scheme!="https" or not p.hostname:raise ValueError("Webhook missing")
    query=dict(parse_qsl(p.query,keep_blank_values=True))
    query["thread_id"]=THREAD_ID
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode(query),p.fragment))

def notify(group,post,webhook):
    started_total=time.monotonic()
    creator="하나요 히요리" if group=="hanayo" else "사키하네 후야"
    title=str(post.get("rss_title") or "리트윗").strip()
    body=post.get("text","")[:2300]
    from x_post_media import resolve_media
    # Repost URLs can point to the original author's tweet. Resolve that author
    # from the URL instead of querying the retweeter's account.
    from urllib.parse import urlsplit
    import re
    # Prefer the original author's X status when the RSS entry exposes it.
    original_url=str(post.get("url",""))
    for key in ("source_link",):
        proposed=str(post.get(key) or "")
        parsed=urlsplit(proposed)
        if parsed.hostname in ("x.com","www.x.com","twitter.com","www.twitter.com"):
            if re.fullmatch(r"/[^/]+/status/[0-9]+",parsed.path):
                original_url="https://x.com"+parsed.path
                break
    match=re.fullmatch(r"/([^/]+)/status/([0-9]+)",urlsplit(original_url).path)
    resolved_images=list(post.get("images") or [])
    photo_known=bool(resolved_images)
    video_preview=""
    has_video=False
    if match:
        found,video_preview,has_video,photo_known=resolve_media(
            dict(post,id=match.group(2)),match.group(1))
        if found:resolved_images=found
    logging.info("X_REPOST_MEDIA_RESOLVED creator=%s id=%s elapsed_seconds=%.2f",group,post.get("id"),time.monotonic()-started_total)
    payload=alert("x",creator,get_banner(group),role_id="",title="🔁 X 리트윗 · "+title[:160],
        body=body,url=original_url,images=resolved_images,media_url=video_preview,date=post.get("pub_date",""),
        handle=handle(group),avatar=avatar(group),ping=False)
    if has_video:
        payload["_abyss_has_video"] = True
    if not photo_known:
        payload["_abyss_photo_unknown"] = True
    if photo_known and not resolved_images and not has_video and not video_preview:
        payload["_abyss_verified_text_only"] = True
    logging.info("X_SEND_MEDIA_GUARD creator=%s post=%s photos=%d video=%s known=%s text_only=%s",
                 group,post.get("id"),len(resolved_images),has_video,photo_known,
                 bool(payload.get("_abyss_verified_text_only")))
    payload["allowed_mentions"]={"parse":[]}
    started=time.monotonic()
    send_webhook(thread_url(webhook),payload)
    logging.info("X_REPOST_DELIVERED creator=%s id=%s source=%s delivery_seconds=%.2f",group,post.get("id",""),post.get("source","?"),time.monotonic()-started)
    logging.info("X_REPOST_TOTAL creator=%s id=%s elapsed_seconds=%.2f",group,post.get("id"),time.monotonic()-started_total)
