"""Shared fanart usage separation for notification vs Discord guild banners.

The guild banner scheduler records used sha256 values. Notification selection
resolves those hashes to original X posts and excludes their entire galleries.
Explicit server-banner-only entries are excluded even before the first rotation.
"""
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
    """Reserve EVERY eligible guild-banner source post from notification reuse.

    Use the real guild-banner selectors, including the wide+fit fallback.
    Previously only widescreen posts were reserved when one wide existed,
    even though the guild picker could select a fitted image of another post.
    """
    if group != "hanayo":
        return set()
    from guild_server_banner import approved_hanayo_art, collected_hanayo_art
    result = set()
    for _path, meta in approved_hanayo_art() + collected_hanayo_art():
        post_id = canonical_post(meta.get("post_url"))
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
    state = read(BASE / "guild_banner_rotation_state.json") if group == "hanayo" else {}
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
    if group == "hanayo" and used:
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
