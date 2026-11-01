"""X retweet/repost notifications: no mentions, dedicated Discord thread."""
import os
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from discord_v2 import alert
from resilience import send_webhook
from banner_manager import get_banner
from creator_profiles import handle,avatar

THREAD_ID="1566509366591627344"

def is_repost(title):
    import re
    return bool(re.match(r"^(?:RT by|Retweeted by)\\s+@?[^:]+:",str(title or ""),re.I))

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
    payload=alert("x",creator,get_banner(group),role_id="",title="🔁 X 리트윗 · "+title[:160],
        body=body,url=post["url"],images=post.get("images",[]),date=post.get("pub_date",""),
        handle=handle(group),avatar=avatar(group),ping=False)
    payload["allowed_mentions"]={"parse":[]}
    send_webhook(thread_url(webhook),payload)
