"""Prevent mixed-character X posts from entering creator-specific fanart banners.

This does not filter original X notification images: their complete galleries remain intact.
Hashtags identify a POST, never which person appears in each attached photo.
"""
import re
from urllib.parse import urlsplit

# Artist-specific fanart tags relevant to the current monitored Stellive creators.
# A post containing the monitored tag plus another character's fanart tag
# cannot be safely split into character-specific images without per-image review.
OTHER_CREATOR_TAGS = {
    "hanayo": {"huyart", "nanart_"},
    "sakihane": {"hiyoriart_", "nanart_"},
}
TAG_PATTERN = re.compile(r"(?<![\w])#([A-Za-z0-9_]+)", re.I)


def hashtags(text):
    return {match.casefold() for match in TAG_PATTERN.findall(str(text or ""))}


def mixed_character_tags(group, text="", tags=None):
    parsed = hashtags(text)
    if tags:
        parsed |= {str(tag).lstrip("#").casefold() for tag in tags}
    return bool(parsed & OTHER_CREATOR_TAGS.get(group, set()))


def post_id_from_url(url):
    try:
        parsed = urlsplit(str(url))
        if parsed.hostname not in ("x.com", "www.x.com", "twitter.com", "www.twitter.com"):
            return ""
        match = re.fullmatch(r"/[A-Za-z0-9_]{1,15}/status/(\d+)", parsed.path)
        return match.group(1) if match else ""
    except (ValueError, TypeError):
        return ""


def blocked_post_ids(queue, group):
    """Include already-cached or approved entries: quarantine old mixed-tag posts."""
    blocked = set()
    for entry in queue.values() if isinstance(queue, dict) else []:
        if not isinstance(entry, dict) or entry.get("group") != group:
            continue
        if mixed_character_tags(group, entry.get("text", ""), entry.get("all_hashtags", [])):
            post_id = str(entry.get("post_id") or post_id_from_url(entry.get("post_url")))
            if post_id.isdigit():
                blocked.add(post_id)
    return blocked


def banner_candidate_allowed(group, entry, queue):
    """Conservatively reject known mixed-character posts from randomized banners."""
    if not isinstance(entry, dict):
        return False
    if mixed_character_tags(group, entry.get("text", ""), entry.get("all_hashtags", [])):
        return False
    post_id = post_id_from_url(entry.get("post_url", ""))
    if post_id and post_id in blocked_post_ids(queue, group):
        return False
    return True
