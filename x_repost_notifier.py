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


def thread_url(webhook):
    p=urlsplit(webhook)
    if p.scheme!="https" or not p.hostname:raise ValueError("Webhook missing")
    query=dict(parse_qsl(p.query,keep_blank_values=True))
    query["thread_id"]=THREAD_ID
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode(query),p.fragment))

def notify(group,post,webhook):
    creator="하나요 히요리" if group=="hanayo" else "사키하네 후야"
    title=str(post.get("rss_title") or "리트윗").strip()
    body=post.get("text","")[:2300]
    from x_post_media import resolve_media_with_video
    # Repost URLs can point to the original author's tweet. Resolve that author
    # from the URL instead of querying the retweeter's account.
    from urllib.parse import urlsplit
    import re
    match=re.fullmatch(r"/([^/]+)/status/([0-9]+)",urlsplit(str(post.get("url",""))).path)
    resolved_images=list(post.get("images") or [])
    photo_known=bool(resolved_images)
    video_preview=""
    direct_video=""
    has_video=False
    if match:
        found,video_preview,has_video,photo_known,direct_video=resolve_media_with_video(
            dict(post,id=match.group(2)),match.group(1))
        if found:resolved_images=found
    payload=alert("x",creator,get_banner(group),role_id="",title="🔁 X 리트윗 · "+title[:160],
        body=body,url=post["url"],images=resolved_images,media_url=direct_video or video_preview,date=post.get("pub_date",""),
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
