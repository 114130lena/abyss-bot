"""Shared X feed classification before notification channel routing.

Reposts and originals are different creator events; a repost may link to the
original artist's *old* status while the creator's repost is fresh.
Only explicit RT markers or confirmed off-account status links authorize
repost routing. Replies are kept out of the original-post notification lane.
"""
import re
from urllib.parse import urlsplit

STATUS = re.compile(r"^/([A-Za-z0-9_]{1,15})/status/(\d+)(?:/)?$")
REPOST = re.compile(
    r"^\s*(?:RT\s+@[A-Za-z0-9_]{1,15}\s*:|RT by\b|"
    r"Retweet(?:ed)? by\b|Repost(?:ed)? by\b|Boosted by\b|"
    r"(?:RT|Retweet|Repost)\s*[:：]|🔁\s*(?:RT\b|Repost\b|@))", re.I)
REPLY = re.compile(r"^\s*(?:R to\b|Reply(?:ing)? to\b|In reply to\b)", re.I)
HOSTS = {"x.com","www.x.com","twitter.com","www.twitter.com",
         "fxtwitter.com","www.fxtwitter.com"}


def status_author(link):
    p = urlsplit(str(link or ""))
    if p.scheme not in ("http","https") or not p.hostname:
        return ""
    if p.hostname.lower() not in HOSTS and not p.hostname.lower().startswith("nitter."):
        return ""
    match = STATUS.fullmatch(p.path)
    return match.group(1).lower() if match else ""


def classify(post, creator):
    """Return repost, reply, original, or unknown without relying on timestamps."""
    if not isinstance(post, dict):
        return "unknown"
    if post.get("repost") is True:
        return "repost"
    title = str(post.get("rss_title") or "")
    description = str(post.get("rss_description") or "")[:500]
    if REPOST.match(title) or REPOST.match(description):
        return "repost"
    source_author = status_author(post.get("source_link"))
    canonical_author = status_author(post.get("url"))
    owner = str(creator or "").lstrip("@").lower()
    if source_author and owner and source_author != owner:
        return "repost"
    if canonical_author and owner and canonical_author != owner:
        return "repost"
    if REPLY.match(title):
        return "reply"
    if post.get("reply") is True or post.get("in_reply_to_status_id"):
        return "reply"
    if source_author == owner or canonical_author == owner:
        return "original"
    return "unknown"
