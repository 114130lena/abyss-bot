"""Shared fanart usage separation for notification vs Discord guild banners.

The guild banner scheduler records used sha256 values. Notification selection
resolves those hashes to original X posts and excludes their entire galleries.
Explicit server-banner-only entries are excluded even before the first rotation.
"""
import os
from urllib.parse import urlsplit
from fanart_discovery import BASE, read


def canonical_post(url):
    """Treat x.com and twitter.com links to one post as the same artwork."""
    p = urlsplit(str(url or ""))
    if p.scheme != "https" or p.hostname not in (
        "x.com", "www.x.com", "twitter.com", "www.twitter.com"
    ):
        return ""
    import re
    match = re.fullmatch(r"/[A-Za-z0-9_]{1,15}/status/([0-9]+)", p.path)
    return match.group(1) if match else ""


def preferred_server_banner(width, height):
    """Preferred Discord server-banner aspect ratios (kept separate from notifications)."""
    if not width or not height or width < 960 or height < 540:
        return False
    aspect = width / height
    return abs(aspect - 16 / 9) <= .035 or abs(aspect - 16 / 10) <= .03


def server_reserved_post_ids(group):
    """Conservatively reserve server-banner posts WITHOUT decoding all images.

    This function runs on the time-critical CHZZK/X/Cafe delivery path. The
    real guild rotation performs PIL validation and SHA256 for every image,
    which took >200 seconds when invoked from each notification. Instead,
    use its existing hashtag/eligibility/blocklist metadata and reserve any
    candidate *potentially* selectable by the guild scheduler. Conservatively
    reserving an invalid/uncached image is safe; exposing a guild-banner image
    in a notification is not.
    """
    if group not in ("hanayo", "sakihane"):
        return set()
    from fanart_discovery import QUEUE, classify
    from fanart_relevance import eligible_hanayo_banner, eligible_huya_banner
    from fanart_vision import enabled as vision_enabled

    result = set()
    if group == "hanayo":
        library = read(BASE / "fanart_index.json")
        entries = library.get("hanayo", []) if isinstance(library, dict) else []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            if not (entry.get("approved") is True and entry.get("permission_confirmed") is True
                    and str(entry.get("hashtag", "")).casefold() == "#hiyoriart_"):
                continue
            if not isinstance(entry.get("original_path"), str) or not entry.get("sha256"):
                continue
            post_id = canonical_post(entry.get("post_url"))
            if post_id:
                result.add(post_id)

    if os.getenv("SERVER_BANNER_USE_COLLECTED", "1") != "1":
        return result

    queue = read(QUEUE)
    if not isinstance(queue, dict):
        return result
    for item in queue.values():
        if not isinstance(item, dict) or item.get("group") != group:
            continue
        tag = "#hiyoriart_" if group == "hanayo" else "#huyart"
        if str(item.get("hashtag", "")).casefold() != tag:
            continue
        if (item.get("decision") == "blocked"
                or item.get("review_status") == "blocked"
                or classify(item.get("text", "")) == "blocked"):
            continue
        if group == "hanayo" and item.get("server_banner_excluded") is True:
            continue
        if group == "hanayo":
            if not vision_enabled() and not eligible_hanayo_banner(item):
                continue
        elif not eligible_huya_banner(item):
            continue
        if not isinstance(item.get("artist"), str) or not item["artist"].startswith("@"):
            continue
        if not any(isinstance(path, str) for path in (item.get("cached_files") or [])[:4]):
            continue
        post_id = canonical_post(item.get("post_url"))
        if post_id:
            result.add(post_id)
    return result

def excluded_notification_art(group):
    """Return (reserved sha256s, original post IDs).

    A currently/previously used guild banner and an explicitly reserved entry
    must never appear in the notification fanart footer.
    """
    index = read(BASE / "fanart_index.json")
    entries = index.get(group, []) if isinstance(index, dict) else []
    state = (read(BASE / "guild_banner_rotation_state.json")
             if group in ("hanayo", "sakihane") else {})
    used = set()
    if isinstance(state, dict):
        used = {s for s in state.get("rotation_used", []) if isinstance(s, str)}
        last = state.get("last_sha256")
        if isinstance(last, str) and last:
            used.add(last)
    reserved_sha = set(used)
    reserved_posts = {x for x in state.get("all_used_post_ids", [])
                      if isinstance(x, str) and x.isdigit()} if isinstance(state, dict) else set()
    # Separate FUTURE server-banner candidates too, not just previously used art.
    reserved_posts.update(server_reserved_post_ids(group))
    # Cached guild-banner artwork lives in the discovery queue, not necessarily
    # the curated index. Exclude any post whose cached artwork was selected.
    if group in ("hanayo", "sakihane") and used:
        queue = read(BASE / "fanart_discovery.json")
        for item in queue.values():
            if not isinstance(item, dict) or item.get("group") != group:
                continue
            for rel in item.get("cached_files", []):
                from pathlib import PurePosixPath
                if isinstance(rel, str) and PurePosixPath(rel).stem in used:
                    post_id = canonical_post(item.get("post_url"))
                    if post_id:
                        reserved_posts.add(post_id)
                    break
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        sha = entry.get("sha256")
        dedicated = entry.get("use_for_server_banner") is True or entry.get("server_banner_only") is True
        if isinstance(sha, str) and (sha in used or dedicated):
            reserved_sha.add(sha)
            pid = canonical_post(entry.get("post_url"))
            if pid:
                reserved_posts.add(pid)
    return reserved_sha, reserved_posts


def notification_allowed(group, entry, exclusions=None):
    if not isinstance(entry, dict):
        return False
    if entry.get("use_for_notification_banner") is False:
        return False
    if entry.get("use_for_server_banner") is True or entry.get("server_banner_only") is True:
        return False
    hashes, posts = exclusions if exclusions is not None else excluded_notification_art(group)
    return (entry.get("sha256") not in hashes
            and canonical_post(entry.get("post_url")) not in posts)
